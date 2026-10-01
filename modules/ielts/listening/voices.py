"""Voice configurations for Edge-TTS audio generation in IELTS Listening

Updated:
- FEMALE_NAMES / MALE_NAMES now include all names used in section_generator.py
  fallbacks (Sonia, Libby, Amin, Leo, etc.) so gender detection works.
- Added more names for variety.
- Added S3 tutor/student name mapping.
"""

import re
from typing import Dict, List, Optional, Union

# =====================================================
# VOICE CONFIGURATIONS (Edge TTS)
# =====================================================
VOICE_CONFIGS: Dict[str, Dict] = {
    'british': {
        'customer': 'en-GB-SoniaNeural',
        'agent': 'en-GB-RyanNeural',
        'guide': 'en-GB-LibbyNeural',
        's3_prof': 'en-GB-RyanNeural',
        's3_maria': 'en-GB-SoniaNeural',
        's3_james': 'en-GB-ThomasNeural',
        's3_sarah': 'en-GB-LibbyNeural',
        's3_student_male': 'en-GB-ThomasNeural',
        's3_student_female': 'en-GB-SoniaNeural',
        's3_student_neutral': 'en-GB-LibbyNeural',
        'lecturer': 'en-GB-SoniaNeural',
        'professor': 'en-GB-RyanNeural',
        'tutor': 'en-GB-RyanNeural',
        'default': 'en-GB-SoniaNeural',
        'fallback': [
            'en-GB-LibbyNeural',
            'en-GB-SoniaNeural',
            'en-GB-RyanNeural',
            'en-GB-ThomasNeural',
        ],
    },
    'australian': {
        'customer': 'en-AU-NatashaNeural',
        'agent': 'en-AU-WilliamNeural',
        'guide': 'en-AU-NatashaNeural',
        's3_prof': 'en-AU-WilliamNeural',
        's3_maria': 'en-AU-NatashaNeural',
        's3_james': 'en-AU-LiamNeural',
        's3_sarah': 'en-AU-CatherineNeural',
        's3_student_male': 'en-AU-LiamNeural',
        's3_student_female': 'en-AU-NatashaNeural',
        's3_student_neutral': 'en-AU-CatherineNeural',
        'lecturer': 'en-AU-NatashaNeural',
        'professor': 'en-AU-WilliamNeural',
        'tutor': 'en-AU-WilliamNeural',
        'default': 'en-AU-NatashaNeural',
        'fallback': [
            'en-AU-NatashaNeural',
            'en-AU-WilliamNeural',
            'en-AU-CatherineNeural',
        ],
    },
    'neutral': {
        'customer': 'en-US-JennyNeural',
        'agent': 'en-US-GuyNeural',
        'guide': 'en-US-JennyNeural',
        's3_prof': 'en-US-GuyNeural',
        's3_maria': 'en-US-JennyNeural',
        's3_james': 'en-US-EricNeural',
        's3_sarah': 'en-US-JennyNeural',
        's3_student_male': 'en-US-EricNeural',
        's3_student_female': 'en-US-JennyNeural',
        's3_student_neutral': 'en-US-JennyNeural',
        'lecturer': 'en-US-JennyNeural',
        'professor': 'en-US-GuyNeural',
        'tutor': 'en-US-GuyNeural',
        'default': 'en-US-JennyNeural',
        'fallback': [
            'en-US-JennyNeural',
            'en-US-GuyNeural',
            'en-US-EricNeural',
        ],
    },
}

# =====================================================
# VOICE METADATA
# =====================================================
VOICE_METADATA: Dict[str, Dict[str, str]] = {
    'en-GB-SoniaNeural': {'gender': 'female', 'age': 'adult', 'style': 'friendly'},
    'en-GB-RyanNeural': {'gender': 'male', 'age': 'adult', 'style': 'professional'},
    'en-GB-LibbyNeural': {'gender': 'female', 'age': 'adult', 'style': 'clear'},
    'en-GB-ThomasNeural': {'gender': 'male', 'age': 'young adult', 'style': 'casual'},
    'en-AU-NatashaNeural': {'gender': 'female', 'age': 'adult', 'style': 'friendly'},
    'en-AU-WilliamNeural': {'gender': 'male', 'age': 'adult', 'style': 'professional'},
    'en-AU-LiamNeural': {'gender': 'male', 'age': 'young adult', 'style': 'casual'},
    'en-AU-CatherineNeural': {'gender': 'female', 'age': 'adult', 'style': 'clear'},
    'en-US-JennyNeural': {'gender': 'female', 'age': 'adult', 'style': 'friendly'},
    'en-US-GuyNeural': {'gender': 'male', 'age': 'adult', 'style': 'professional'},
    'en-US-EricNeural': {'gender': 'male', 'age': 'young adult', 'style': 'casual'},
}

# =====================================================
# SECTION VOICE MAP (with role-based keys)
# =====================================================
SECTION_VOICE_MAP: Dict[int, Dict[str, str]] = {
    1: {
        'Customer': 'customer', 'Patient': 'customer', 'Student': 'customer',
        'Passenger': 'customer', 'Applicant': 'customer', 'Tenant': 'customer',
        'Caller': 'customer', 'Client': 'customer', 'Guest': 'customer',
        'Agent': 'agent', 'Receptionist': 'agent', 'Administrator': 'agent',
        'Officer': 'agent', 'HR Manager': 'agent', 'Landlord': 'agent',
        'Staff': 'agent', 'Bank Officer': 'agent', 'Insurance Officer': 'agent',
        'Travel Agent': 'agent', 'Booking Agent': 'agent', 'Assistant': 'agent',
        'Desk Clerk': 'agent', 'Service Agent': 'agent',
    },
    2: {
        'Guide': 'guide', 'Speaker': 'guide', 'Narrator': 'guide',
        'Presenter': 'guide', 'Announcer': 'guide',
    },
    3: {
        'Professor': 's3_prof', 'Tutor': 's3_prof', 'Dr': 's3_prof',
        'Teacher': 's3_prof', 'Lecturer': 's3_prof',
        'Maria': 's3_maria', 'James': 's3_james', 'Sarah': 's3_sarah',
        'Student': 's3_student_neutral',
        'Student1': 's3_student_neutral',
        'Student2': 's3_student_neutral',
        'Student3': 's3_student_neutral',
    },
    4: {
        'Lecturer': 'lecturer', 'Presenter': 'lecturer',
        'Professor': 'lecturer', 'Dr': 'lecturer',
        'Speaker': 'lecturer',
    },
}

# =====================================================
# FEMALE NAMES
# Includes all names used by section_generator.py fallbacks:
# Maria, Sarah, Emma, Sonia, Libby, Jane, Anna, Mary, Linda,
# Sophie, Charlotte, Amelia, Olivia, Isabella, Mia, Ella, Grace
# Also includes additional names for variety.
# =====================================================
FEMALE_NAMES = {
    # --- Core names from section_generator.py fallback list ---
    'Maria', 'Sarah', 'Emma', 'Sonia', 'Libby', 'Jane', 'Anna', 'Mary', 'Linda',
    'Sophie', 'Charlotte', 'Amelia', 'Olivia', 'Isabella', 'Mia', 'Ella', 'Grace',
    # --- Existing names (kept) ---
    'Sophia', 'Ava', 'Harper', 'Evelyn', 'Abigail', 'Emily',
    'Elizabeth', 'Sofia', 'Avery', 'Madison', 'Scarlett', 'Victoria',
    'Aria', 'Chloe', 'Camila', 'Penelope', 'Riley', 'Layla', 'Zoey',
    'Nora', 'Lily', 'Eleanor', 'Hannah', 'Lillian', 'Addison', 'Aubrey',
    'Ellie', 'Stella', 'Natalie', 'Zoe', 'Leah', 'Hazel', 'Violet', 'Aurora',
    'Savannah', 'Audrey', 'Brooklyn', 'Bella', 'Claire', 'Skylar', 'Lucy',
    'Paisley', 'Everly', 'Caroline', 'Genesis', 'Emilia', 'Kennedy',
    'Samantha', 'Maya', 'Nova', 'Aaliyah', 'Elena', 'Naomi', 'Lisa',
    'Karen', 'Betty', 'Helen', 'Donna', 'Carol', 'Ruth', 'Sharon',
    'Michelle', 'Laura', 'Deborah', 'Jessica', 'Amanda', 'Melissa', 'Kimberly',
    'Tina', 'Sandra', 'Angela', 'Pamela', 'Nicole', 'Katherine',
    'Susan', 'Jennifer', 'Amy', 'Rebecca', 'Stephanie', 'Rachel', 'Heather',
    # --- NEW: International variety ---
    'Priya', 'Aisha', 'Mei', 'Yuki', 'Fatima', 'Zara', 'Ananya', 'Leila',
    'Elena', 'Nadia', 'Chiara', 'Ingrid', 'Amara', 'Nia', 'Rina',
    'Lucia', 'Beatrice', 'Freya', 'Astrid', 'Yasmin', 'Hana', 'Sofia',
    'Isla', 'Poppy', 'Ruby', 'Willow', 'Ivy', 'Rose', 'Daisy',
}

# =====================================================
# MALE NAMES
# Includes all names used by section_generator.py fallbacks:
# James, David, Leo, Amin, Thomas, Ryan, John, Robert, Michael,
# William, Alexander, Daniel, Matthew, Andrew, Oliver, George
# Also includes additional names for variety.
# =====================================================
MALE_NAMES = {
    # --- Core names from section_generator.py fallback list ---
    'James', 'David', 'Leo', 'Amin', 'Thomas', 'Ryan', 'John', 'Robert', 'Michael',
    'William', 'Alexander', 'Daniel', 'Matthew', 'Andrew', 'Oliver', 'George',
    # --- Existing names (kept) ---
    'Joseph', 'Charles', 'Christopher', 'Anthony', 'Mark', 'Donald', 'Steven',
    'Paul', 'Joshua', 'Kenneth', 'Kevin', 'Brian', 'Timothy', 'Ronald', 'Edward',
    'Jason', 'Jeffrey', 'Jacob', 'Gary', 'Nicholas', 'Eric',
    'Jonathan', 'Stephen', 'Larry', 'Justin', 'Scott', 'Brandon', 'Benjamin',
    'Samuel', 'Raymond', 'Gregory', 'Frank', 'Patrick', 'Jack',
    'Dennis', 'Jerry', 'Tyler', 'Aaron', 'Jose', 'Nathan', 'Adam', 'Henry',
    'Zachary', 'Todd', 'Ethan', 'Noah', 'Logan', 'Mason', 'Elijah',
    'Liam', 'Lucas', 'Aiden', 'Carter', 'Jayden', 'Grayson', 'Gabriel',
    'Angel', 'Dylan', 'Caleb', 'Isaac', 'Luke', 'Evan', 'Owen', 'Levi',
    'Hunter', 'Wyatt', 'Hudson', 'Miles', 'Asher', 'Jaxon', 'Eli',
    # --- NEW: International variety ---
    'Rahul', 'Aarav', 'Kenji', 'Hiro', 'Omar', 'Yusuf', 'Carlos', 'Marco',
    'Pablo', 'Erik', 'Ivan', 'Kwame', 'Tunde', 'Chen', 'Jin', 'Andrei',
    'Stefan', 'Dmitri', 'Hassan', 'Ali', 'Akira', 'Jorge', 'Luca',
    'Finn', 'Oscar', 'Arthur', 'Alfie', 'Freddie',
}


def detect_gender(name: str) -> Optional[str]:
    """
    Detect gender from a name using common name lists.

    Args:
        name: The speaker name to check.

    Returns:
        'male', 'female', or None if unknown.
    """
    if not name:
        return None

    # Clean name - remove titles and take first word
    clean = re.sub(r'^(Dr\.?|Prof\.?|Mr\.?|Mrs\.?|Ms\.?|Miss\.?|Sir|Dame)\s*', '', name, flags=re.IGNORECASE)
    clean = clean.split()[0].strip() if clean.split() else name

    # Case-insensitive lookup
    clean_lower = clean.lower()
    female_lower = {n.lower() for n in FEMALE_NAMES}
    male_lower = {n.lower() for n in MALE_NAMES}

    if clean_lower in female_lower:
        return 'female'
    if clean_lower in male_lower:
        return 'male'

    return None


# =====================================================
# MAIN VOICE RESOLUTION
# =====================================================

def get_voice(
    accent: str = "british",
    speaker: str = "",
    section: int = 1,
    gender: Optional[str] = None,
) -> str:
    """
    Get the appropriate Edge-TTS voice for a given speaker and section.

    Args:
        accent: 'british', 'australian', or 'neutral'
        speaker: Speaker name/role (e.g., 'Customer', 'Professor')
        section: Listening section number (1-4)
        gender: Optional override for gender ('male' or 'female')

    Returns:
        Valid Edge-TTS voice name
    """
    if not speaker:
        speaker = ""

    accent_key = accent.lower()
    config = VOICE_CONFIGS.get(accent_key, VOICE_CONFIGS['british'])

    # ---- Section 3: academic discussion ----
    if section == 3 and speaker:
        section_map = SECTION_VOICE_MAP.get(section, {})
        if speaker in section_map:
            voice_key = section_map[speaker]
            if voice_key in config:
                return config[voice_key]

        detected_gender = gender or detect_gender(speaker)
        if detected_gender == 'female':
            return config.get('s3_student_female', config['default'])
        elif detected_gender == 'male':
            return config.get('s3_student_male', config['default'])

        return config.get('s3_student_neutral', config['default'])

    # ---- Section 1: conversation ----
    if section == 1:
        section_map = SECTION_VOICE_MAP.get(section, {})
        for role, voice_key in section_map.items():
            if re.search(rf'\b{re.escape(role)}\b', speaker, re.IGNORECASE):
                if voice_key in config:
                    return config[voice_key]

        detected_gender = gender or detect_gender(speaker)
        if detected_gender == 'female':
            return config.get('customer', config['default'])
        elif detected_gender == 'male':
            return config.get('agent', config['default'])

        return config.get('default', 'en-GB-SoniaNeural')

    # ---- Sections 2 & 4: monologue ----
    if section in (2, 4):
        section_map = SECTION_VOICE_MAP.get(section, {})
        for role, voice_key in section_map.items():
            if re.search(rf'\b{re.escape(role)}\b', speaker, re.IGNORECASE):
                if voice_key in config:
                    return config[voice_key]

    # ---- Fallback: direct speaker key in config ----
    speaker_lower = speaker.lower()
    for key, value in config.items():
        if key.lower() == speaker_lower:
            return value

    # ---- Last resort: role-based partial match ----
    for key, value in config.items():
        if key.lower() in ['customer', 'agent', 'guide', 'lecturer', 'professor', 'tutor']:
            if re.search(rf'\b{key}\b', speaker_lower, re.IGNORECASE):
                return value

    return config.get('default', 'en-GB-SoniaNeural')


# =====================================================
# HELPER FUNCTIONS
# =====================================================

def get_fallback_voices(accent: str = "british") -> List[str]:
    """Return fallback voices for a given accent."""
    config = VOICE_CONFIGS.get(accent.lower(), VOICE_CONFIGS['british'])
    return config.get('fallback', ['en-GB-SoniaNeural'])


def get_voice_metadata(voice_name: str) -> Optional[Dict[str, str]]:
    """Get metadata for a voice."""
    return VOICE_METADATA.get(voice_name)


def list_available_accents() -> List[str]:
    """Return list of supported accents."""
    return list(VOICE_CONFIGS.keys())


def get_voices_by_section(section: int, accent: str = "british") -> List[str]:
    """Get all available voice names for a section and accent."""
    config = VOICE_CONFIGS.get(accent.lower(), VOICE_CONFIGS['british'])
    section_map = SECTION_VOICE_MAP.get(section, {})

    voices = []
    for voice_key in section_map.values():
        if voice_key in config:
            voices.append(config[voice_key])

    voices.extend(config.get('fallback', []))

    seen = set()
    unique_voices = []
    for v in voices:
        if v not in seen:
            seen.add(v)
            unique_voices.append(v)
    return unique_voices


# =====================================================
# SINGLETON – accessible as `voices`
# =====================================================

class _Voices:
    """Wrapper class to provide a clean API for voice resolution."""

    @staticmethod
    def get_voice(accent: str = "british", speaker: str = "", section: int = 1, gender: Optional[str] = None) -> str:
        return get_voice(accent, speaker, section, gender)

    @staticmethod
    def get_fallback_voices(accent: str = "british") -> List[str]:
        return get_fallback_voices(accent)

    @staticmethod
    def get_voice_metadata(voice_name: str) -> Optional[Dict[str, str]]:
        return get_voice_metadata(voice_name)

    @staticmethod
    def list_available_accents() -> List[str]:
        return list_available_accents()

    @staticmethod
    def get_voices_by_section(section: int, accent: str = "british") -> List[str]:
        return get_voices_by_section(section, accent)

    @staticmethod
    def detect_gender(name: str) -> Optional[str]:
        return detect_gender(name)


# Instantiate a singleton for easy import
voices = _Voices()