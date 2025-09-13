from __future__ import annotations

from magent2.team import TeamRegistry, reset_registry_for_testing


def test_register_and_find_owner_by_glob() -> None:
    reset_registry_for_testing()
    reg = TeamRegistry()
    reg.register_agent(
        team_name="A",
        agent_name="Alpha",
        responsibilities=["build"],
        allowed_paths=["src/app/**", "README.md"],
    )
    reg.register_agent(
        team_name="A",
        agent_name="Bravo",
        responsibilities=["docs"],
        allowed_paths=["docs/**"],
    )

    owner1 = reg.find_owner_for_path("src/app/main.py")
    owner2 = reg.find_owner_for_path("docs/intro.md")
    owner3 = reg.find_owner_for_path("README.md")
    owner4 = reg.find_owner_for_path("setup.cfg")

    assert owner1 and owner1.agent_name == "Alpha"
    assert owner2 and owner2.agent_name == "Bravo"
    assert owner3 and owner3.agent_name == "Alpha"
    assert owner4 is None


def test_window_person_set_get() -> None:
    reset_registry_for_testing()
    reg = TeamRegistry()
    reg.set_window_person("TeamX", "alice@example.com")
    assert reg.get_window_person("TeamX") == "alice@example.com"
