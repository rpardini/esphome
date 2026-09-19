"""transmit_rc6_mce fills its data and keeps a 0/1 toggle per action."""

from collections.abc import Callable
from pathlib import Path

from tests.component_tests.remote_transmitter import protocol_call_bodies


def test_rc6_mce_fields_and_toggle(
    generate_main: Callable[[str | Path], str],
    component_config_path: Callable[[str], Path],
) -> None:
    main_cpp = generate_main(component_config_path("rc6_mce.yaml"))
    bodies = protocol_call_bodies(main_cpp)
    assert len(bodies) == 2
    toggle = "apply_call.data.toggle = []() { static uint8_t toggle = 1; return toggle ^= 1; }();"
    for body in bodies:
        assert toggle in body
    assert "apply_call.data.address = 0x800F;" in bodies[0]
    assert "apply_call.data.command = 0x40C;" in bodies[0]
    assert "apply_call.data.address = 0x1234;" in bodies[1]
    assert "RC6MCEProtocol" in main_cpp
