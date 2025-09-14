"""Configuration system for testwriter agent to work across different repositories."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, ConfigDict


@dataclass
class RepoConfig:
    """Configuration for a specific repository structure."""
    
    # Repository identification
    name: str
    root_markers: List[str]  # Files that identify this repo type (e.g., ["pyproject.toml", "setup.py"])
    
    # Source code structure
    source_dirs: List[str]  # Directories containing source code (e.g., ["src", "lib", "core"])
    test_dirs: List[str]    # Directories for tests (e.g., ["tests", "test"])
    exclude_patterns: List[str]  # Patterns to exclude (e.g., ["__pycache__", "*.pyc"])
    
    # Coverage configuration
    coverage_targets: List[str]  # Modules to measure coverage for (e.g., ["core", "src"])
    coverage_min_threshold: float  # Minimum coverage threshold (0.0-1.0)
    
    # Test execution
    test_runner: str  # Test runner command (e.g., "pytest", "python -m pytest")
    test_patterns: List[str]  # Test file patterns (e.g., ["test_*.py", "*_test.py"])
    
    # Module introspection
    import_prefix: str  # Prefix for imports (e.g., "core", "src", "myproject")
    module_extensions: List[str]  # File extensions to analyze (e.g., [".py"])
    
    # Output configuration
    output_subdir: str  # Subdirectory for generated tests (e.g., "generated", "auto")


class TestWriterConfig(BaseModel):
    """Main configuration for testwriter agent."""
    
    model_config = ConfigDict(extra="forbid")
    
    # Repository configuration
    repo_config: RepoConfig
    
    # Generation limits
    max_tests_per_run: int = 100  # Allow many tests per run
    max_modules_per_run: int = 50  # Allow all modules
    max_repair_attempts: int = 2
    pass_rate_threshold: float = 0.6  # 60% pass rate to move to next module
    
    # Timing configuration
    cooldown_seconds: float = 120.0
    timeout_seconds: int = 180
    
    # LLM configuration
    llm_temperature: float = 0.1
    llm_max_tokens: int = 800
    force_json: bool = True
    
    # Pure topological: No priority weights - context validity drives execution


# Predefined repository configurations
REPO_CONFIGS = {
    "ranger": RepoConfig(
        name="Ranger",
        root_markers=["pyproject.toml", "core/", "agents/"],
        source_dirs=["core", "agents"],
        test_dirs=["tests"],
        exclude_patterns=["__pycache__", "*.pyc", "*.pyo", ".git", ".pytest_cache"],
        coverage_targets=["core"],
        coverage_min_threshold=0.7,
        test_runner="python -m pytest",
        test_patterns=["test_*.py"],
        import_prefix="core",
        module_extensions=[".py"],
        output_subdir="generated"
    ),
    
    "django": RepoConfig(
        name="Django Project",
        root_markers=["manage.py", "settings.py", "requirements.txt"],
        source_dirs=[".", "apps"],
        test_dirs=["tests", "test"],
        exclude_patterns=["__pycache__", "*.pyc", "migrations", "static", "media"],
        coverage_targets=["."],
        coverage_min_threshold=0.8,
        test_runner="python manage.py test",
        test_patterns=["test_*.py", "tests.py"],
        import_prefix="",
        module_extensions=[".py"],
        output_subdir="auto_tests"
    ),
    
    "fastapi": RepoConfig(
        name="FastAPI Project",
        root_markers=["main.py", "app/", "requirements.txt"],
        source_dirs=["app", "src"],
        test_dirs=["tests", "test"],
        exclude_patterns=["__pycache__", "*.pyc", ".pytest_cache", "alembic"],
        coverage_targets=["app", "src"],
        coverage_min_threshold=0.75,
        test_runner="python -m pytest",
        test_patterns=["test_*.py"],
        import_prefix="app",
        module_extensions=[".py"],
        output_subdir="generated"
    ),
    
    "package": RepoConfig(
        name="Python Package",
        root_markers=["setup.py", "pyproject.toml", "src/"],
        source_dirs=["src", "lib"],
        test_dirs=["tests", "test"],
        exclude_patterns=["__pycache__", "*.pyc", "build", "dist", "*.egg-info"],
        coverage_targets=["src"],
        coverage_min_threshold=0.8,
        test_runner="python -m pytest",
        test_patterns=["test_*.py"],
        import_prefix="src",
        module_extensions=[".py"],
        output_subdir="generated"
    )
}


def detect_repo_type(repo_path: Path) -> Optional[str]:
    """Detect repository type based on file markers."""
    
    for repo_type, config in REPO_CONFIGS.items():
        markers_found = 0
        
        for marker in config.root_markers:
            marker_path = repo_path / marker
            if marker_path.exists():
                markers_found += 1
        
        # If at least half the markers are found, consider it a match
        if markers_found >= len(config.root_markers) // 2:
            return repo_type
    
    return None


def create_config(repo_path: Path, repo_type: Optional[str] = None) -> TestWriterConfig:
    """Create testwriter configuration for a repository."""
    
    if repo_type is None:
        repo_type = detect_repo_type(repo_path)
    
    if repo_type is None or repo_type not in REPO_CONFIGS:
        # Default to generic Python package configuration
        repo_type = "package"
    
    repo_config = REPO_CONFIGS[repo_type]
    
    return TestWriterConfig(
        repo_config=repo_config
    )


def get_source_files(repo_path: Path, config: TestWriterConfig) -> List[Path]:
    """Get all source files in the repository based on configuration."""
    
    source_files = []
    
    for source_dir in config.repo_config.source_dirs:
        source_path = repo_path / source_dir
        if not source_path.exists():
            continue
        
        for ext in config.repo_config.module_extensions:
            pattern = f"**/*{ext}"
            
            for file_path in source_path.glob(pattern):
                # Check exclusion patterns
                should_exclude = False
                for exclude_pattern in config.repo_config.exclude_patterns:
                    if exclude_pattern in str(file_path):
                        should_exclude = True
                        break
                
                if not should_exclude and file_path.is_file():
                    source_files.append(file_path)
    
    return source_files


def get_test_directory(repo_path: Path, config: TestWriterConfig) -> Path:
    """Get the test directory for generated tests."""
    
    # Try to find existing test directory
    for test_dir in config.repo_config.test_dirs:
        test_path = repo_path / test_dir
        if test_path.exists():
            return test_path / config.repo_config.output_subdir
    
    # Default to first test directory
    return repo_path / config.repo_config.test_dirs[0] / config.repo_config.output_subdir


def get_coverage_command(config: TestWriterConfig) -> List[str]:
    """Get coverage command based on configuration."""
    
    cmd = config.repo_config.test_runner.split()
    
    # Add coverage flags
    if "pytest" in config.repo_config.test_runner:
        for target in config.repo_config.coverage_targets:
            cmd.extend([f"--cov={target}"])
        cmd.extend(["--cov-report=xml", "--cov-report=json", "--cov-report=term"])
    
    return cmd


def get_module_import_path(file_path: Path, repo_path: Path, config: TestWriterConfig) -> str:
    """Get the import path for a module file."""
    
    # Get relative path from repo root
    rel_path = file_path.relative_to(repo_path)
    
    # Remove file extension
    module_path = str(rel_path.with_suffix(""))
    
    # Convert path separators to dots
    module_path = module_path.replace("/", ".").replace("\\", ".")
    
    # Add import prefix if configured
    if config.repo_config.import_prefix:
        # Check if the path already starts with the prefix
        if not module_path.startswith(config.repo_config.import_prefix):
            module_path = f"{config.repo_config.import_prefix}.{module_path}"
    
    return module_path


def validate_config(config: TestWriterConfig, repo_path: Path) -> List[str]:
    """Validate configuration against repository structure."""
    
    issues = []
    
    # Check if source directories exist
    for source_dir in config.repo_config.source_dirs:
        source_path = repo_path / source_dir
        if not source_path.exists():
            issues.append(f"Source directory '{source_dir}' not found")
    
    # Check if at least one test directory can be created
    test_dirs_exist = any(
        (repo_path / test_dir).exists() 
        for test_dir in config.repo_config.test_dirs
    )
    
    if not test_dirs_exist:
        issues.append("No test directories found or can be created")
    
    # Check coverage targets
    for target in config.repo_config.coverage_targets:
        target_path = repo_path / target
        if not target_path.exists():
            issues.append(f"Coverage target '{target}' not found")
    
    return issues
