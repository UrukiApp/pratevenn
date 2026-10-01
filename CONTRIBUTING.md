## Contribution Guidelines

Thank you for considering contributing to this project!
Contributions are always welcome and appreciated.

### How to Contribute

Please check the [issue tracker](https://github.com/UrukiApp/pratevenn/issues) to see if there is an issue
you
would like to work on or if it has already been resolved.

#### Reporting Bugs

1. Open an issue on the [issue tracker](https://github.com/UrukiApp/pratevenn/issues).
2. Include information such as steps to reproduce the observed behavior and relevant logs or screenshots.

#### Suggesting Features

1. Open an issue on the [issue tracker](https://github.com/UrukiApp/pratevenn/issues).
2. Provide details about the feature, its purpose, and potential implementation ideas.

### Submitting Pull Requests

- Make sure all tests pass before submitting a pull request.
- Write a clear description of the changes you made and the reasons behind them.

> [!IMPORTANT]
> It's assumed that by submitting a pull request, you agree to license your contributions under the project's license.

### Development Workflow

> [!IMPORTANT]
> If you're using an AI-assisted coding tool like Claude Code or Codex, make sure the AI follows the instructions in the [AGENTS.md](AGENTS.md) file.

#### Prerequisites

Install GNU Make if it's not already installed on your system.

```shell
# Install Make on Debian, Ubuntu, and other Debian-based systems.
sudo apt-get install make
```

- Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Run `make install` (or `make setup`) to create the environment and install the locked application and development dependencies.
- Run `make models` once to download model weights, then `make run` to start Pratevenn.

#### Code Style

- Use the `make format` command to format the code.

#### Running Tests

- Use the `make test` command to run the tests.

#### See Available Commands

- Run `make help` to see all available commands for managing different tasks.

### Code of Conduct

We adhere to the project's [Code of Conduct](CODE_OF_CONDUCT.md).
