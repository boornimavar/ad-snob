import socket

import demo


def test_free_port_skips_a_busy_one():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        port = busy.getsockname()[1]
        assert demo.free_port(port, tries=5) != port
