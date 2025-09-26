TESTS_GEN_SCHEMA = {
  "type": "object",
  "properties": {
    "files": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "path": {"type": "string"},
          "content": {"type": "string"}
        },
        "required": ["path", "content"]
      }
    }
  },
  "required": ["files"]
}

RUN_RESULT_SCHEMA = {
  "type": "object",
  "properties": {
    "passed": {"type": "boolean"},
    "return_code": {"type": "integer"},
    "stdout": {"type": "string"},
    "stderr": {"type": "string"},
    "failed_tests": {"type": "array", "items": {"type": "string"}},
    "summary": {
      "type": "object",
      "properties": {
        "passed": {"type": "integer"},
        "failed": {"type": "integer"},
        "skipped": {"type": "integer"}
      }
    }
  },
  "required": ["passed"]
}

COVERAGE_SCHEMA = {
  "type": "object",
  "properties": {
    "total": {"type": "number"},
    "files": {"type": "array"}
  },
  "required": ["total"]
}


