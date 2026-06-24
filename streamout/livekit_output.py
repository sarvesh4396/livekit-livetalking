###############################################################################
#  Output — LiveKit 直播输出
###############################################################################

import asyncio
import threading
import numpy as np
import cv2
from livekit import rtc

from streamout.base_output import BaseOutput
from registry import register
from utils.logger import logger
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from avatars.base_avatar import BaseAvatar


@register("streamout", "livekit")
class LiveKitOutput(BaseOutput):
    """LiveKit output — joins a LiveKit room as a participant and publishes
    lip-sync video + avatar audio directly into the room.

    Manages its own asyncio event loop in a background thread so the
    synchronous render pipeline can call push_video_frame / push_audio_frame
    without blocking.
    """

    def __init__(self, opt=None, parent: Optional["BaseAvatar"] = None, **kwargs):
        super().__init__(opt, parent)
        self.livekit_url   = getattr(opt, "livekit_url",   "")
        self.livekit_token = getattr(opt, "livekit_token", "")
        self.fps           = getattr(opt, "fps", 25)

        self._room:         Optional[rtc.Room]             = None
        self._video_source: Optional[rtc.VideoSource]      = None
        self._audio_source: Optional[rtc.AudioSource]      = None
        self._video_track:  Optional[rtc.LocalVideoTrack]  = None
        self._audio_track:  Optional[rtc.LocalAudioTrack]  = None

        self._loop:    Optional[asyncio.AbstractEventLoop] = None
        self._thread:  Optional[threading.Thread]          = None
        self._connected = threading.Event()
        self._video_published = threading.Event()

    # ------------------------------------------------------------------
    # BaseOutput interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the asyncio event loop thread. Connection happens via connect()."""
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="livekit_out")
        self._thread.start()
        logger.info("LiveKitOutput loop started — awaiting connect() call")

    def connect(self, url: str, token: str) -> None:
        """Connect (or reconnect) to a LiveKit room. Thread-safe; blocks until connected."""
        self.livekit_url = url
        self.livekit_token = token
        if self._loop is None:
            self.start()
        self._connected.clear()
        asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        if not self._connected.wait(timeout=30):
            raise RuntimeError("LiveKit connection timed out after 30s")
        logger.info("LiveKitOutput ready")

    def push_video_frame(self, frame) -> None:
        if not isinstance(frame, np.ndarray) or self._loop is None or not self._connected.is_set():
            return

        h, w = frame.shape[:2]

        # Lazy-create VideoSource + publish track on first frame
        if self._video_source is None:
            self._video_source = rtc.VideoSource(width=w, height=h)
            self._video_track  = rtc.LocalVideoTrack.create_video_track(
                "avatar_video", self._video_source
            )
            fut = asyncio.run_coroutine_threadsafe(
                self._room.local_participant.publish_track(
                    self._video_track,
                    rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA),
                ),
                self._loop,
            )
            try:
                fut.result(timeout=10)
                self._video_published.set()
                logger.info("LiveKitOutput: video track published (%dx%d)", w, h)
            except Exception:
                logger.exception("LiveKitOutput: failed to publish video track")
                return

        rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
        lk_frame = rtc.VideoFrame(
            width=w,
            height=h,
            type=rtc.VideoBufferType.RGBA,
            data=rgba.tobytes(),
        )
        self._video_source.capture_frame(lk_frame)  # thread-safe

    def push_audio_frame(self, frame: np.ndarray, eventpoint=None) -> None:
        if self._audio_source is None or self._loop is None or not self._connected.is_set():
            if self.parent:
                self.parent.notify(eventpoint)
            return

        # frame is np.float32 from the pipeline; convert to int16 for LiveKit
        if frame.dtype != np.int16:
            pcm = (frame * 32767.0).clip(-32768, 32767).astype(np.int16)
        else:
            pcm = frame

        lk_frame = rtc.AudioFrame(
            data=pcm.tobytes(),
            sample_rate=16000,
            num_channels=1,
            samples_per_channel=len(pcm),
        )
        asyncio.run_coroutine_threadsafe(
            self._audio_source.capture_frame(lk_frame), self._loop
        )
        if self.parent:
            self.parent.notify(eventpoint)

    def get_buffer_size(self) -> int:
        return 0

    def stop(self) -> None:
        if self._room and self._loop and self._loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._room.disconnect(), self._loop)
            try:
                fut.result(timeout=5)
            except Exception:
                pass
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=6)
        logger.info("LiveKitOutput stopped")

    # ------------------------------------------------------------------
    # Internal asyncio loop (runs in background thread)
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self) -> None:
        # Disconnect previous room if reconnecting
        if self._room:
            try:
                await self._room.disconnect()
            except Exception:
                pass

        self._room = rtc.Room()
        # Reset video so it's re-published on the new room's first frame
        self._video_source = None
        self._video_track = None
        self._video_published.clear()

        # Publish audio track immediately on connect (no lazy-init needed)
        self._audio_source = rtc.AudioSource(sample_rate=16000, num_channels=1)
        self._audio_track  = rtc.LocalAudioTrack.create_audio_track(
            "avatar_audio", self._audio_source
        )

        await self._room.connect(self.livekit_url, self.livekit_token)
        logger.info("LiveKitOutput: connected to room %s", self._room.name)

        await self._room.local_participant.publish_track(
            self._audio_track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        logger.info("LiveKitOutput: audio track published")

        self._connected.set()
