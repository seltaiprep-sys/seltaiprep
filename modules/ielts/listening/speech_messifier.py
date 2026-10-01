"""Post-process scripts to sound like natural speech."""

import random
import re
from typing import List, Optional


class SpeechMessifier:
    """
    A processor that adds hesitations, fillers, and trailing uncertainty
    to dialogue scripts to make them sound more natural.
    """

    # Default hesitations and fillers
    HESITATIONS = ["Um", "Er", "Oh", "Hmm", "Well"]
    FILLERS = ["I mean", "you know", "actually", "like", "sort of"]
    UNCERTAINTY_ENDINGS = [" I think.", " if that is right.", " I am not sure."]

    # Default list of speakers considered "customers" (non‑expert)
    CUSTOMER_SPEAKERS = ["Customer", "Patient", "Student", "Passenger", "Tenant", "Applicant"]

    def __init__(self, customer_speakers: Optional[List[str]] = None):
        """
        Initialize the messifier with an optional custom list of customer speakers.

        Args:
            customer_speakers: List of speaker labels that should receive
                               hesitation and uncertainty markers.
                               If None, uses the default list.
        """
        if customer_speakers is not None:
            self.customer_speakers = customer_speakers
        else:
            self.customer_speakers = self.CUSTOMER_SPEAKERS

    def messify(self, script: str, intensity: float = 0.35, preserve_labels: bool = True) -> str:
        """
        Process a script to add natural speech patterns.

        Args:
            script: The full script as a multi-line string.
            intensity: Probability scaling factor (0.0 to 1.0).
                       Higher values increase the chance of insertions.
            preserve_labels: If True, lines without speaker labels are left untouched.
                             If False, unlabelled lines are processed as well
                             (but without speaker-specific logic).

        Returns:
            The modified script.
        """
        if not script:
            return script

        lines = script.strip().split('\n')
        result = []
        for line in lines:
            if ':' in line:
                speaker, text = line.split(':', 1)
                speaker = speaker.strip()
                text = text.strip()
                text = self._messify_text(text, intensity, speaker)
                result.append(f"{speaker}: {text}")
            elif preserve_labels:
                result.append(line)
            else:
                # No speaker label – process as neutral (e.g., monologue)
                text = line.strip()
                text = self._messify_text(text, intensity, speaker=None)
                result.append(text)

        return '\n'.join(result)

    def _messify_text(self, text: str, intensity: float, speaker: Optional[str]) -> str:
        """
        Apply natural speech modifications to a single line of dialogue.

        Args:
            text: The speaker's utterance.
            intensity: Probability scaling factor.
            speaker: The speaker label (or None for monologue).

        Returns:
            The modified text.
        """
        if len(text) < 10:
            return text

        words = text.split()
        is_customer = speaker in self.customer_speakers

        # 1. Add hesitation at the beginning (only for customer speakers)
        if is_customer and random.random() < intensity * 0.7:
            h = random.choice(self.HESITATIONS)
            # Avoid adding if text already starts with a similar hesitation
            if not any(text.lower().startswith(w.lower()) for w in self.HESITATIONS):
                # Preserve original capitalisation: insert hesitation followed by a comma,
                # and keep the original first letter as is (do not lowercase).
                text = f"{h}, {text}"

        # 2. Insert filler mid‑sentence
        if len(words) > 7 and random.random() < intensity * 0.5:
            pos = len(words) // 2
            filler = random.choice(self.FILLERS)
            # Insert with surrounding commas, but avoid double punctuation
            # We'll insert " , filler, " to keep spacing clean.
            words.insert(pos, f", {filler},")
            text = " ".join(words)

        # 3. Add trailing uncertainty (customer speakers only)
        if is_customer and random.random() < intensity * 0.4:
            # Check if text already ends with a sentence‑ending punctuation
            if text[-1] in ('.', '!', '?'):
                # Append without extra period
                ending = random.choice(self.UNCERTAINTY_ENDINGS).rstrip('.')
                text += ending
            else:
                text += random.choice(self.UNCERTAINTY_ENDINGS)

        return text


# Singleton instance with default settings
speech_messifier = SpeechMessifier()