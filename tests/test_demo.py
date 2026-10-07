import socket
from types import SimpleNamespace

import demo


def test_free_port_skips_a_busy_one():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        port = busy.getsockname()[1]
        assert demo.free_port(port, tries=5) != port


def test_no_app_running_means_none():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unused = sock.getsockname()[1]
    assert demo.running_app_url(unused) is None


def test_idle_time_counts_from_the_last_page_visit_but_not_during_a_search():
    server = SimpleNamespace(_scrapes_in_progress=set(), last_page_contact=1000)
    assert demo.idle_for(server, now=1600) == 600

    server._scrapes_in_progress.add(("skincare", "IN"))
    assert demo.idle_for(server, now=1600) == 0
