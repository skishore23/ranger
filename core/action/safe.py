"""Functional error handling with Either types for pure morphisms."""

from __future__ import annotations
import asyncio
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TypeVar, Generic, Union, Callable, Awaitable, Any, Dict, List
from pathlib import Path

T = TypeVar('T')
U = TypeVar('U')
E = TypeVar('E')


@dataclass(frozen=True)
class Left(Generic[E]):
    """Left side of Either - represents error/failure."""
    left: E
    tag: str = "Left"
    
    def map(self, f: Callable[[Any], U]) -> Left[E]:
        """Map does nothing on Left - error propagates."""
        return self
    
    def flat_map(self, f: Callable[[Any], Either[E, U]]) -> Left[E]:
        """FlatMap does nothing on Left - error propagates."""
        return self
    
    def fold(self, left: Callable[[E], U], right: Callable[[Any], U]) -> U:
        """Fold applies left function to error value."""
        return left(self.left)


@dataclass(frozen=True)
class Right(Generic[T]):
    """Right side of Either - represents success/value."""
    right: T
    tag: str = "Right"
    
    def map(self, f: Callable[[T], U]) -> Right[U]:
        """Map transforms the success value."""
        return Right(f(self.right))
    
    def flat_map(self, f: Callable[[T], Either[E, U]]) -> Either[E, U]:
        """FlatMap enables monadic composition."""
        return f(self.right)
    
    def fold(self, left: Callable[[Any], U], right: Callable[[T], U]) -> U:
        """Fold applies right function to success value."""
        return right(self.right)


# Either type alias
Either = Union[Left[E], Right[T]]


@dataclass(frozen=True)
class SafeError:
    """Structured error information."""
    message: str
    error_type: str
    context: Dict[str, Any]


def safe(func: Callable[[], T]) -> Either[SafeError, T]:
    """Execute function safely, returning Either instead of raising."""
    try:
        result = func()
        return Right(result)
    except Exception as e:
        return Left(SafeError(
            message=str(e),
            error_type=type(e).__name__,
            context={}
        ))


async def safe_async(func: Callable[[], Awaitable[T]]) -> Either[SafeError, T]:
    """Execute async function safely, returning Either instead of raising."""
    try:
        result = await func()
        return Right(result)
    except Exception as e:
        return Left(SafeError(
            message=str(e),
            error_type=type(e).__name__,
            context={}
        ))


def safe_subprocess(
    cmd: List[str], 
    cwd: Path, 
    timeout: int = 60,
    capture_output: bool = True
) -> Either[SafeError, subprocess.CompletedProcess]:
    """Execute subprocess safely with Either return type."""
    return safe(lambda: subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture_output,
        text=True,
        timeout=timeout
    ))


def safe_file_read(path: Path) -> Either[SafeError, str]:
    """Read file safely with Either return type."""
    return safe(lambda: path.read_text())


def safe_file_write(path: Path, content: str) -> Either[SafeError, None]:
    """Write file safely with Either return type."""
    return safe(lambda: path.write_text(content))


def safe_json_parse(text: str) -> Either[SafeError, Any]:
    """Parse JSON safely with Either return type."""
    import json
    return safe(lambda: json.loads(text))


# Utility functions for common Either operations
def sequence_either(eithers: List[Either[E, T]]) -> Either[E, List[T]]:
    """Convert List[Either[E, T]] to Either[E, List[T]] - fail fast on first error."""
    results = []
    for either in eithers:
        if either.tag == "Left":
            return either
        results.append(either.right)
    return Right(results)


def traverse_either(items: List[T], f: Callable[[T], Either[E, U]]) -> Either[E, List[U]]:
    """Map function over list, collecting results or failing fast."""
    return sequence_either([f(item) for item in items])
