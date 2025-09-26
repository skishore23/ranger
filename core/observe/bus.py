"""In-memory observation bus for human collaboration."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from datetime import datetime
import uuid


@dataclass(frozen=True)
class ReviewCard:
    """A review card posted for human collaboration."""
    
    id: str
    title: str
    description: Optional[str] = None
    fields: List[Dict[str, Any]] = field(default_factory=list)
    capability_id: str = ""
    workspace_data: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    status: str = "pending"  # pending, submitted, cancelled


@dataclass(frozen=True)
class Submission:
    """A human submission in response to a review card."""
    
    id: str
    card_id: str
    data: Dict[str, Any]
    submitted_at: datetime = field(default_factory=datetime.now)


class ObservationBus:
    """In-memory bus for posting review cards and receiving submissions."""
    
    def __init__(self):
        """Initialize empty observation bus."""
        self._cards: Dict[str, ReviewCard] = {}
        self._submissions: Dict[str, List[Submission]] = {}
        self._handlers: List[Callable[[ReviewCard], None]] = []
    
    def post_review_card(
        self,
        title: str,
        description: Optional[str] = None,
        fields: Optional[List[Dict[str, Any]]] = None,
        capability_id: str = "",
        workspace_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Post a review card for human collaboration.
        
        Args:
            title: Title of the review card
            description: Optional description
            fields: Form fields for human input
            capability_id: ID of capability requesting review
            workspace_data: Relevant workspace data
            
        Returns:
            Card ID
        """
        card_id = str(uuid.uuid4())
        card = ReviewCard(
            id=card_id,
            title=title,
            description=description,
            fields=fields or [],
            capability_id=capability_id,
            workspace_data=workspace_data or {},
        )
        
        self._cards[card_id] = card
        self._submissions[card_id] = []
        
        # Notify handlers
        for handler in self._handlers:
            handler(card)
        
        return card_id
    
    def submit_response(self, card_id: str, data: Dict[str, Any]) -> str:
        """Submit a response to a review card.
        
        Args:
            card_id: ID of the review card
            data: Response data
            
        Returns:
            Submission ID
            
        Raises:
            KeyError: If card_id doesn't exist
        """
        if card_id not in self._cards:
            raise KeyError(f"Review card {card_id} not found")
        
        submission_id = str(uuid.uuid4())
        submission = Submission(
            id=submission_id,
            card_id=card_id,
            data=data,
        )
        
        self._submissions[card_id].append(submission)
        
        # Mark card as submitted
        card = self._cards[card_id]
        self._cards[card_id] = ReviewCard(
            id=card.id,
            title=card.title,
            description=card.description,
            fields=card.fields,
            capability_id=card.capability_id,
            workspace_data=card.workspace_data,
            created_at=card.created_at,
            status="submitted",
        )
        
        return submission_id
    
    def get_card(self, card_id: str) -> Optional[ReviewCard]:
        """Get a review card by ID.
        
        Args:
            card_id: Card ID
            
        Returns:
            ReviewCard if found, None otherwise
        """
        return self._cards.get(card_id)
    
    def get_submissions(self, card_id: str) -> List[Submission]:
        """Get all submissions for a review card.
        
        Args:
            card_id: Card ID
            
        Returns:
            List of submissions
        """
        return self._submissions.get(card_id, [])
    
    def get_latest_submission(self, card_id: str) -> Optional[Submission]:
        """Get the latest submission for a review card.
        
        Args:
            card_id: Card ID
            
        Returns:
            Latest submission if any, None otherwise
        """
        submissions = self.get_submissions(card_id)
        return submissions[-1] if submissions else None
    
    def add_handler(self, handler: Callable[[ReviewCard], None]) -> None:
        """Add a handler for new review cards.
        
        Args:
            handler: Function to call when new cards are posted
        """
        self._handlers.append(handler)
    
    def list_pending_cards(self) -> List[ReviewCard]:
        """List all pending review cards.
        
        Returns:
            List of pending cards
        """
        return [card for card in self._cards.values() if card.status == "pending"]
    
    def list_all_cards(self) -> List[ReviewCard]:
        """List all review cards.
        
        Returns:
            List of all cards
        """
        return list(self._cards.values())
