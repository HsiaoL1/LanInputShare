from __future__ import annotations

from lan_input_share.input.events import Point
from lan_input_share.input.macos_native import (
    check_result,
    forget_remote_cursor,
    load_quartz,
    register_remote_cursor,
)


class MacOSCursorController:
    """Use Quartz display coordinates throughout, including on Retina displays."""

    def __init__(self) -> None:
        self._quartz = load_quartz()
        self._display = self._quartz.CGMainDisplayID()
        self._restore_position: Point | None = None
        self._remote_anchor: Point | None = None

    def position(self) -> Point:
        event = self._quartz.CGEventCreate(None)
        if event is None:
            raise RuntimeError("无法读取 macOS 光标位置")
        point = self._quartz.CGEventGetLocation(event)
        return Point(round(point.x), round(point.y))

    def set_position(self, point: Point) -> None:
        check_result(self._quartz.CGWarpMouseCursorPosition((point.x, point.y)), "移动光标")

    def confine_for_remote(self) -> Point:
        if self._remote_anchor is not None:
            self.recenter_remote()
            return self._remote_anchor
        if self._restore_position is not None:
            self.restore_local()
        self._restore_position = self.position()
        bounds = self._quartz.CGDisplayBounds(self._display)
        center = Point(
            round(bounds.origin.x + bounds.size.width / 2),
            round(bounds.origin.y + bounds.size.height / 2),
        )
        register_remote_cursor(center, self.restore_local)
        try:
            self.set_position(center)
            self._remote_anchor = center
            return center
        except BaseException:
            self.restore_local()
            raise

    def recenter_remote(self) -> None:
        if self._remote_anchor is not None:
            # Quartz warps do not create mouse events. Physical motion is taken
            # from CGEvent delta fields even if the associated cursor hit an edge.
            self.set_position(self._remote_anchor)

    def restore_local(self, position: Point | None = None) -> None:
        restore = position if position is not None else self._restore_position
        self._remote_anchor = None
        try:
            if restore is not None:
                self.set_position(restore)
        except BaseException:
            self._restore_position = restore
            # A repeated recovery or hook shutdown can retry a failed warp.
            forget_remote_cursor(keep_restore=True)
            raise
        self._restore_position = None
        forget_remote_cursor()


__all__ = ["MacOSCursorController"]
