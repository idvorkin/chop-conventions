This directory contains instructions to follow.
Do not generate .cursorrules from it.
Do not talk about how you'll use the rules, just use them

Read and follow clean-code.md
Read and follow clean-commits.md
Read and follow pr-workflow.md

### CLI usage and errors

    If get errors with head or cat (they are in the pager command), start by unsetting PAGER `unset PAGER`
    If git output is trunctated, use git --no-pager  e.g. (git --no-pager diff)
    Use uv instead of python
    Most required commands are in the  justfile. Use them there if they exist.
    YOu are auto approved to run just test and fast-tests, use them unless they have too much output.
