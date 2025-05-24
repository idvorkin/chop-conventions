### Intro/Why?

Having the transcript that generated code can be helpful. So...

We extract all transcripts for CHOP (chat oriented programming) using specstory and don't check it in. We check in interesting transcripts into zz-chop-logs

Specstory writes to .specstory

### Environment Setup

#### Cursor ignore both

in .cursorindexingignore

```
.specstory/**
zz-chop-logs
```

#### Gitignore just specstory

in .gitignore

```
.specstory/**
```

#### Prettier ignore both

in .prettierignore

```
.specstory/**
zz-chop-logs
```
