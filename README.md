# Chop Conventions

> Development conventions, best practices, and standardized workflows for productive AI-assisted coding

## Intro

This repository contains codified development practices, conventions, and setup guidelines designed to maximize productivity when working with AI coding assistants. **These conventions are designed to be pulled into many projects**—copy, symlink, or reference them as needed.

The magic of "vibe coding" lies in AI's ability to infer intent from minimal input, but that magic becomes fragile without proper specifications and verification processes.

This collection of conventions serves as the **specifications layer** that makes AI-assisted development consistent, reliable, and scalable across projects. Use this repo as a central source of truth, pulling in the conventions you need for each project.

## Why These Conventions Matter

As explained in [Vibe Coding: Best Practices for Flow, Fun, and Results](https://idvork.in/vibe), successful AI-assisted development requires:

- **Specifications**: Clear constraints and guidelines for business logic, architecture, code style, and environment setup
- **Verification**: Robust testing and validation processes

Without these foundations, AI coding can lead to inconsistent results and frustrating debugging sessions. These conventions provide the scaffolding needed to harness AI's power effectively.

## Getting Started

Paste this into Cursor.

Read the directions in https://github.com/idvorkin/chop-conventions/tree/main/dev-setup and then apply it

## What's Included

### 🛠 Development Environment

- Comprehensive `.gitignore` configurations covering 10+ programming languages
- Editor and IDE setup guidelines (VSCode, IntelliJ, Vim, etc.)
- Platform-specific configurations (macOS, Windows, Linux)
- Security best practices for credentials and sensitive files

### 📋 Code Standards

- Language-specific best practices
- Clean code guidelines
- Architecture decision frameworks
- Testing and verification strategies

### 🔧 Tooling & Automation

- Build tool configurations
- CI/CD pipeline templates
- Pre-commit hooks and automated checks
- Development workflow optimization
- **[Beads](./dev-setup/beads.md)** - Git-backed issue tracking for AI agents

## Philosophy

These conventions embody the principle that **constraints enable creativity**. By establishing clear guidelines upfront, you free AI (and human developers) to focus on solving problems rather than making endless micro-decisions about formatting, structure, and tooling.

The goal is to create a feedback loop where:

1. **Specifications** guide AI output toward desired outcomes
2. **Verification** catches issues early and validates results
3. **Iteration** refines both specs and verification based on learnings

## Usage

1. **Start with Setup**: Follow the [dev-setup guides](./dev-setup/) to establish your foundation
2. **Customize for Your Project**: Adapt the conventions to your specific needs
3. **Iterate and Improve**: Update conventions based on what you learn
4. **Share Across Projects**: Reuse successful patterns in new codebases

## Learn More

- 📖 **[Vibe Coding Practices](https://idvork.in/vibe)** - Deep dive into AI-assisted development philosophy
- 🛠 **[Dev Setup Guide](./dev-setup/)** - Practical implementation instructions
- 💡 **[Contributing](./CONTRIBUTING.md)** - How to improve these conventions _(coming soon)_

---

_Part of [idvork.in](https://idvork.in)'s ecosystem for thoughtful technology practices._
