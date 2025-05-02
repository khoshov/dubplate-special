<details>
<summary>### Installation UV</summary>

Install uv with our standalone installers:

```bash
# On macOS and Linux.
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
# On Windows.
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Or, from [PyPI](https://pypi.org/project/uv/):

```bash
# With pip.
pip install uv
```

If installed via the standalone installer, uv can update itself to the latest version:

```bash
uv self update
```

See the [installation documentation](https://docs.astral.sh/uv/getting-started/installation/) for
details and alternative installation methods.

</details>

---

To install a specific Python version:
```bash
uv python install 3.13
```

Sync the project's dependencies with the environment.
```bash 
uv sync
```
Run a command in the project environment.
```bash
uv run <COMMAND>
```
Run django server.
```bash
uv run manage.py runserver
```


### RUFF:
```bash
uvx ruff
```

```bash
uvx ruff check .
```


