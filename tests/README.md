# Vietnam Hearts Agent Tests

This directory contains comprehensive tests for the Vietnam Hearts Agent, designed to work with both pytest and standalone execution.

## Test Structure

### Files

- `test_agent_pytest.py` - Main pytest-compatible test suite
- `test_agent_local.py` - Original standalone test script
- `run_agent_tests.py` - Test runner that can use pytest or standalone mode
- `pytest.ini` - Pytest configuration file
- `logs/` - Directory containing test logs (created automatically)

### Test Categories

The tests are organized into several categories with pytest markers:

- **Unit Tests** (`@pytest.mark.unit`) - Fast, isolated tests
- **Integration Tests** (`@pytest.mark.integration`) - Tests that involve multiple components
- **Agent Tests** (`@pytest.mark.agent`) - Agent-specific functionality
- **Knowledge Base Tests** (`@pytest.mark.knowledge_base`) - Knowledge base functionality
- **Quick Reply Tests** (`@pytest.mark.quick_replies`) - Quick reply functionality

## Running Tests

### Option 1: Using the Test Runner (Recommended)

```bash
# Run all tests (auto-detects pytest or falls back to standalone)
python tests/run_agent_tests.py

# Force pytest mode
python tests/run_agent_tests.py --pytest

# Force standalone mode
python tests/run_agent_tests.py --standalone

# Run only unit tests
python tests/run_agent_tests.py --markers unit

# Run only agent tests
python tests/run_agent_tests.py --markers agent

# Run integration tests with knowledge base
python tests/run_agent_tests.py --markers integration knowledge_base
```

### Option 2: Using Pytest Directly

```bash
# Install pytest if not already installed
pip install pytest

# Run all tests
pytest tests/test_agent_pytest.py -v

# Run specific test categories
pytest tests/test_agent_pytest.py -m "unit" -v
pytest tests/test_agent_pytest.py -m "integration" -v
pytest tests/test_agent_pytest.py -m "agent" -v

# Run tests with detailed output
pytest tests/test_agent_pytest.py -v -s

# Run tests and generate HTML report
pytest tests/test_agent_pytest.py --html=tests/logs/report.html
```

### Option 3: Standalone Mode

```bash
# Run the original test script
python tests/test_agent_local.py
```

## Test Logging

All tests generate detailed logs in the `tests/logs/` directory:

### Log Files

- `test_agent_[test_name]_[timestamp].log` - Individual test logs
- `test_summary_[timestamp].log` - Test session summary
- `pytest.log` - Pytest framework logs

### Log Content

Each test log includes:
- Test execution details
- Input/output data
- Assertion results
- Error messages and stack traces
- Performance metrics

### Example Log Structure

```
2024-01-15 10:30:45 - test_agent_volunteer_intent - INFO - Testing volunteer intent detection
2024-01-15 10:30:45 - test_agent_volunteer_intent - INFO - Test case 1: Basic volunteer interest
2024-01-15 10:30:45 - test_agent_volunteer_intent - INFO - Input: 'I want to volunteer'
2024-01-15 10:30:45 - test_agent_volunteer_intent - INFO - Intent: volunteer (expected: volunteer)
2024-01-15 10:30:45 - test_agent_volunteer_intent - INFO - Confidence: 0.95
2024-01-15 10:30:45 - test_agent_volunteer_intent - INFO - Escalate: False
```

## Test Coverage

### Import Tests
- Agent module imports
- Model imports
- Configuration imports
- Knowledge base imports

### Functionality Tests
- Volunteer intent detection
- FAQ intent detection with knowledge base
- Unknown intent detection
- Response generation
- Confidence scoring

### Quick Reply Tests
- Signup quick reply
- Learn more quick reply
- Contact quick reply
- FAQ quick reply

### Knowledge Base Tests
- Knowledge base initialization
- Search functionality
- Content retrieval
- Source citations

### Environment Tests
- Required environment variables
- Optional environment variables
- Configuration validation

## Configuration

### Pytest Configuration (`pytest.ini`)

```ini
[tool:pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = 
    -v
    --tb=short
    --strict-markers
    --disable-warnings
    --log-cli-level=INFO
    --log-file=tests/logs/pytest.log
    --log-file-level=INFO
markers =
    slow: marks tests as slow
    integration: marks tests as integration tests
    unit: marks tests as unit tests
    agent: marks tests as agent-related tests
    knowledge_base: marks tests as knowledge base tests
    quick_replies: marks tests as quick reply tests
```

### Environment Variables

Required for full functionality:
- `GEMINI_API_KEY` - Google Gemini API key
- `NEW_USER_SIGNUP_LINK` - Volunteer signup link

Optional:
- `GEMINI_MODEL` - Gemini model name
- `FACEBOOK_MESSENGER_LINK` - Facebook Messenger link
- `INSTAGRAM_LINK` - Instagram link
- `FACEBOOK_PAGE_LINK` - Facebook page link

## Troubleshooting

### Common Issues

1. **Import Errors**
   - Ensure you're running from the project root
   - Check that all dependencies are installed
   - Verify Python path includes project root

2. **Pytest Not Found**
   - Install pytest: `pip install pytest`
   - Use standalone mode as fallback

3. **API Key Issues**
   - Set `GEMINI_API_KEY` in your environment
   - Tests will run with reduced functionality without it

4. **Log Directory Issues**
   - Logs directory is created automatically
   - Check file permissions if creation fails

### Debug Mode

For detailed debugging, run tests with verbose output:

```bash
# Pytest with maximum verbosity
pytest tests/test_agent_pytest.py -v -s --tb=long

# Standalone with debug logging
python tests/test_agent_local.py
```

## Continuous Integration

The test suite is designed to work in CI/CD environments:

- Logs are written to files for later analysis
- Exit codes indicate success/failure
- Tests can run without external dependencies (with reduced functionality)
- Markers allow selective test execution

## Contributing

When adding new tests:

1. Use appropriate pytest markers
2. Include comprehensive logging
3. Add descriptive test names and docstrings
4. Ensure tests are isolated and repeatable
5. Update this README if adding new test categories 