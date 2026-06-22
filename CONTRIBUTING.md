# Contributing to Nocept

Thank you for considering contributing to Nocept! We welcome contributions from everyone—regardless of experience level, background, or identity. 

Whether you're fixing a typo, reporting a bug, suggesting an improvement, or adding a new feature, **your contribution matters**. This guide will help you get started.

## Types of Contributions

### 💬 Non-Code Contributions (Equally Valuable!)

- **Documentation**: Improve READMEs, tutorials, API docs, or fix typos
- **Bug Reports**: Help us find and understand issues (even if you can't fix them)
- **Feature Ideas**: Suggest improvements or new capabilities
- **Discussion**: Share your use cases, challenges, or feedback
- **Testing**: Try Nocept in different environments and let us know what works
- **Translation**: Help translate docs or comments to other languages
- **Community**: Answer questions, mentor others, spread the word

**Don't underestimate the value of good documentation and community support.** Many projects need help here more than code!

### 🐛 Reporting Bugs

1. **Check existing issues** first — your bug may already be reported
2. **Create a new GitHub issue** with:
   - Clear title and description
   - Steps to reproduce
   - Expected vs. actual behavior
   - Python version, OS, and dependencies (output of `pip freeze`)
   - Error logs/tracebacks

### Requesting Features

1. **Describe the use case** — what problem does it solve?
2. **Propose a solution** or outline how it might work
3. **Link related issues** if applicable

### 💻 Submitting Code

1. **Fork the repository** and create a branch for your feature/fix
   ```bash
   git checkout -b feature/my-feature
   ```

2. **Write clear code** (it doesn't have to be perfect!)
   - Follow existing style where reasonable (Black, isort for Python)
   - Add tests if you can (we can help add them if you're unsure)
   - Keep commits small and descriptive when possible

3. **Run tests locally**
   ```bash
   pytest tests/ -v
   ```
   Don't worry if you're not sure how to test — the maintainers can help!

4. **Update documentation** (if applicable)
   - Add/update docstrings for new functions
   - Update README/API docs if behavior changes
   - Link to related issues

5. **Submit a pull request**
   - Reference the issue it solves: "Fixes #123"
   - Describe what changed and why
   - Don't worry about perfection — we'll iterate together
   - Be prepared for constructive feedback (it's about the code, not you)

**Note**: Pull request reviews may take time. We're all volunteers. Thanks for your patience!

## Getting Help

**Stuck? Need guidance? Don't know where to start?**

- 📖 Check [README.md](README.md) and [ARCHITECTURE.md](ARCHITECTURE.md)
- 💬 Open a [GitHub Discussion](https://github.com/okayteakay/nocept/discussions) — ask questions freely
- 🎯 Look for issues tagged `good-first-issue` or `help-wanted`
- 📝 Comment on an issue to ask for guidance before diving in

**First-time contributor?** We're here to help. No question is too basic.

## Your First Contribution

If you're new to open source:

1. **Start small**: Fix a typo, improve documentation, or report a bug
2. **Ask questions**: Comment on an issue asking for feedback before you start
3. **Don't worry about perfection**: We'll help refine your contribution
4. **Be respectful**: Everyone here is volunteering their time
5. **Have fun**: Contributing should be enjoyable!

## Development Setup

```bash
# Clone and install in development mode
git clone https://github.com/okayteakay/nocept.git
cd nocept
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Format code
black . && isort .

# Type check (if mypy is installed)
mypy agent models
```

## Code Guidelines

These guidelines help keep the codebase maintainable and welcoming for everyone:

- **Python 3.11+** — we target this version for compatibility
- **Type hints encouraged** — they help others understand your code (but not required)
- **Docstrings for public functions** — a brief summary + Args/Returns is helpful
- **Clear comments** — explain the "why" for non-obvious logic (readers don't need "what" — the code shows that)
- **Keep it generic** — no company-specific references (so anyone can use it)
- **Tests appreciated** — even a simple test is better than none. We can help write them!

**Not sure if your code matches these guidelines?** That's okay — maintainers can help refine it.

## Commit Message Format

```
Short, imperative summary (50 chars max)

Optional detailed explanation. Wrap at 72 chars.
Reference issues: Fixes #123, relates to #456.
```

Example:
```
Fix self-duplicate false positive in classifier

When exceptions are saved before classification, check_duplicate()
was finding the current exception and flagging it as a duplicate of
itself. Now we pass exception_id and exclude it from the check.

Fixes #42
```

## Areas for Contribution

**Looking for something to work on?** Here are some ideas:

### 🚀 High-Impact Contributions
- **Documentation**: Improve READMEs, add tutorials, fix typos
- **Testing**: Write tests, test in different environments
- **Bug fixes**: Fix reported issues

### 💡 Feature Ideas
- **Document ingestion**: Support new formats (XML, EDI, SAP)
- **Decision gates**: Add new approval criteria or improve existing ones
- **Performance**: Optimize LLM calls, caching, or Redis queries
- **Observability**: Better logging, metrics, or tracing

### 📚 All Skill Levels Welcome

## Community & Support

We're committed to maintaining a **welcoming, inclusive community**:

- 📖 Read our [Code of Conduct](CODE_OF_CONDUCT.md)
- 💬 Ask questions in [GitHub Discussions](https://github.com/okayteakay/nocept/discussions)
- 🐛 Report bugs in [GitHub Issues](https://github.com/okayteakay/nocept/issues)
- 🤝 Be respectful and kind to all contributors
- 🎓 Share your knowledge and help others learn

---

## Questions?

- **How do I start?** → Read [Your First Contribution](#your-first-contribution) above
- **I found a bug** → Open a [GitHub Issue](https://github.com/okayteakay/nocept/issues)
- **I have a question** → Use [GitHub Discussions](https://github.com/okayteakay/nocept/discussions)
- **I want to contribute code** → Fork the repo and follow [Submitting Code](#-submitting-code)
- **I'm not sure what to do** → Reach out! We're happy to help.

---

## Thank You! 🎉

Thank you for contributing to Nocept and helping make AP automation better for everyone. We appreciate:

- Your time and effort
- Your creativity and ideas
- Your patience with the review process
- Your kindness and respect for fellow contributors

You're awesome! Keep being awesome. 💪
