# Task Completion Checklist

## Before Submitting Code

### 1. Code Quality
- [ ] Run formatter: `hatch run style:fmt`
- [ ] Check style: `hatch run style:check`
- [ ] No linting errors

### 2. Testing
- [ ] Run relevant tests: `hatch run test:test`
- [ ] Add tests for new functionality
- [ ] All tests pass

### 3. Documentation
- [ ] Update docstrings for new/modified functions
- [ ] Update README.md if adding new features
- [ ] Update CLAUDE.md if changing architecture

### 4. Git Hygiene
- [ ] Meaningful commit messages
- [ ] No sensitive data (API keys, credentials)
- [ ] No large binary files (use Git LFS if needed)

## For Style-Bert-VITS2 Changes
```bash
cd Style-Bert-VITS2
hatch run style:check
hatch run test:test
```

## For New AILoveShen Components
- Follow existing patterns in Style-Bert-VITS2
- Use async/await for I/O operations
- Implement proper error handling
- Add logging with loguru
