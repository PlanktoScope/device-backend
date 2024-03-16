"""streams provides support for synchronization of image streams across threads."""

import io
import threading
import typing

import typing_extensions
from readerwriterlock import rwlock

Value = typing.TypeVar("Value")


class Latest(typing.Generic[Value]):
    """A thread-safe stream which enables multiple threads to monitor a changing value.

    This stream is designed to support at-most-once delivery, so no guarantees are made about
    delivery of every value to the consumers: a consumer will skip values when it's too busy
    overloaded/blocked. This is a design feature to prevent backpressure on certain consumers
    (e.g. from downstream clients sending the value across a network, when the value is a large
    image) from degrading stream quality for everyone.

    Note that no thread synchronization is managed for the value itself; consumers should avoid
    modifying the value once they have access to it, or else they should coordinate with each other
    to prevent data races.
    """

    def __init__(self) -> None:
        """Initialize the stream."""
        self._latest_value: typing.Optional[Value] = None
        # Mutex to prevent data races between readers and writers:
        self._latest_value_lock = rwlock.RWLockWrite()
        # Condition variable to allow listeners to wait for a new value:
        self._available = threading.Condition()

    def write(self, value: Value) -> None:
        """Write a new value to the stream for consumption by readers.

        If readers are accessing the value when this method is called, then it may block for a
        while, in order to wait for those readers to finish.
        """
        with self._latest_value_lock.gen_wlock():
            self._latest_value = value
        with self._available:
            self._latest_value = value
            self._available.notify_all()

    def wait_next(self) -> None:
        """Wait until the next value is available.

        When called, this method blocks until it is awakened by a `update()` call in another
        thread. Once awakened, it returns.
        """
        with self._available:
            self._available.wait()

    def get(self) -> typing.Optional[Value]:
        """Return the latest value in the stream."""
        with self._latest_value_lock.gen_rlock():
            return self._latest_value


class LatestByteBuffer(io.BufferedIOBase):
    """A thread-safe stream of discrete byte buffers for use with the picamera2 library.

    This stream is designed to support at-most-once delivery, so no guarantees are made about
    delivery of every buffer to the consumers: a consumer will skip buffers when it's too busy
    overloaded/blocked. This is a design feature to prevent backpressure on certain consumers
    (e.g. from downstream clients sending the buffer across a network, when the buffer is a large
    image) from degrading stream quality for everyone.

    Note that no thread synchronization is managed for any buffer; consumers must avoid modifying
    the buffer once they have access to it.

    This stream can be used by anything which requires a [io.BufferedIOBase], assuming it never
    splits buffers.
    """

    def __init__(self) -> None:
        """Initialize the stream."""
        self._latest = Latest[bytes]()

    def write(self, buffer: typing_extensions.Buffer) -> int:
        """Write the byte buffer as the latest buffer in the stream.

        If readers are accessing the buffer when this method is called, then it may block for a
        while, in order to wait for those readers to finish.

        Returns:
            The length of the byte buffer written.
        """
        b = bytes(buffer)
        self._latest.write(b)
        return len(b)

    def wait_next(self) -> None:
        """Wait until the next buffer is available.

        When called, this method blocks until it is awakened by a `update()` call in another
        thread. Once awakened, it returns.
        """
        return self._latest.wait_next()

    def get(self) -> typing.Optional[bytes]:
        """Return the latest value in the stream."""
        return self._latest.get()
