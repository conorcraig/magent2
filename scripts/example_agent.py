from agents import Agent, Runner


def main() -> None:
    agent = Agent(name="Smoke", instructions="Reply with a short haiku about recursion.")
    result = Runner.run_sync(agent, "Test")
    print(result.final_output)


if __name__ == "__main__":
    main()
