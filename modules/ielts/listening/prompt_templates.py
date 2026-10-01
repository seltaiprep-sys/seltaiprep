"""AI prompt templates for IELTS Listening test generation.

  IMPORTANT (v4):
    This module is a REFERENCE / ALTERNATIVE prompt source. The active
    prompts used by SectionGenerator live inline in `section_generator.py`
    because they integrate with TopicRegistry, forced speakers, and
    per-topic data lookup. Both files must stay in sync if you edit either.

    If you want a single source of truth, delete the inline `_build_*`
    methods in section_generator.py and route everything through this
    module instead — but you'll lose TopicRegistry integration.

PHILOSOPHY (v2 — Natural Conversation Focus):
  - Prompts are LEAN. AI gets 1 example + essential rules.
  - Each section has ONE reference example showing the target naturalness.
  - Word count is mentioned ONCE (soft target, not hard gate).
  - Speaker personalities (Section 3) are passed through, not abstracted.

FIXES APPLIED (v3):
  (1) Section 1 — explicit 8-distinct-category list.
  (2) Section 1 — good/bad examples make the rule concrete.
  (3) Section 4 — distinct-category rule applied (12→8 from a list).

FIXES APPLIED (v4):
  (A) Word targets aligned with section_generator.SECTION_TARGET_WORDS.
  (B) SECTION4_DATA_MAP expanded to cover all topics from
         get_section4_topics() (previously only 7 of 17 had real data).
  (C) Header clarifies this module's role vs the inline prompts.

Compatible with the following imports from `section_generator.py`:
    from .prompt_templates import (
        build_section1_prompt_ai,
        build_section2_prompt,
        build_section3_prompt,
        build_section4_prompt,
        SECTION4_DATA_MAP,
    )
"""

from typing import Dict, List, Optional


# ============================================================
# SHARED BLOCKS
# ============================================================

STRICT_JSON_BLOCK = """
=== JSON OUTPUT RULES ===
- Return VALID JSON only. No markdown, no extra text.
- Double quotes only. No trailing commas.
- EXACTLY 10 questions.
- Each question: "question", "answer", "type".
- For multiple_choice and matching questions: include "options" array.
- Escape newlines in the script as \\n.
"""

NATURAL_SPEECH_GUIDE = """
=== NATURAL SPEECH GUIDANCE ===
- Use contractions: "I'm", "don't", "we'll", "that's".
- Include fillers in Sections 1 & 3: "um", "well", "actually", "let me think".
- Vary sentence length — mix short reactions ("Right.", "Oh!", "I see.") with
  longer explanations.
- Show natural reactions: "That's interesting", "Really?", "Hmm, good question".
- Allow speakers to change their mind or correct themselves mid-sentence.
"""


# ============================================================
# QUESTION TYPE BLOCKS
# ============================================================

SECTION1_QUESTION_TYPES = """
=== QUESTIONS (Section 1) ===
- Q1–Q8: form_completion (text inputs)
- Q9–Q10: multiple_choice with EXACTLY 3 options labelled A, B, C

 CRITICAL — PICK 8 DISTINCT CATEGORIES (NO OVERLAP) 
Each of Q1–Q8 MUST come from a DIFFERENT category. You may NOT ask two
questions about the same type of information. Pick 8 out of this list:

  1. Full name (spelled out)
  2. Contact phone number
  3. Email address OR postal address (pick ONE)
  4. Appointment DATE
  5. Type of appointment or service requested
  6. Room / vehicle / plan / membership type
  7. Number of guests / people / items
  8. Cost / fee / deposit (pick ONE — not two)
  9. Payment method OR payment plan (pick ONE)
  10. Special request / preference
  11. Reference / booking number
  12. Any other unique factual field (invent one)

 SPECIFIC FORBIDDEN COMBINATIONS:
     Q4: Appointment DATE + Q5: Appointment TIME
     Q6: Cost X + Q7: Cost Y
     Q2: Phone number + Q3: Mobile number
     Q3: Email + Q7: Postal address

 GOOD EXAMPLE (8 distinct categories, no overlap):
    Q1: Full name: → "Emma Wilson"
    Q2: Contact phone number: → "07700 900123"
    Q3: Email address: → "emma.wilson@email.com"
    Q4: Appointment date: → "15 March"
    Q5: Reason for visit: → "Routine check-up"
    Q6: Preferred appointment time: → "Morning"
    Q7: Total cost: → "£85"
    Q8: Reference number: → "REF-2847"

Example MC:
{"question": "What does the monthly plan include?", "type": "multiple_choice",
 "options": ["A: Two check-ups and hygiene visits per year",
             "B: One check-up and one hygiene visit per year",
             "C: Unlimited check-ups and hygiene visits"],
 "answer": "B"}
"""

SECTION2_QUESTION_TYPES = """
=== QUESTIONS (Section 2) ===
- Q11–Q14: matching (3–4 options). Include an "options" array.
- Q15: table_completion (text input — a number or name).
- Q16–Q18: map_labeling. Each MUST include:
    "type": "map_labeling"
    "map_title": e.g. "Ground Floor Plan"
    "map_locations": [{"letter": "A", "name": "Main Entrance"}, ...]
    "correct_answer": one letter (e.g. "A")
  All three questions must share the SAME map_title and SAME map_locations.
- Q19–Q20: multiple_choice with 3 options (A, B, C).

 Each of the 10 answers MUST be DIFFERENT — no repeats in the answer key.
 For the 3 map_labeling questions: each must have a DISTINCT correct letter.
"""

SECTION3_QUESTION_TYPES = """
=== QUESTIONS (Section 3) ===
- Q21–Q24: matching — match speakers to opinions/topics.
- Q25–Q27: note_completion (text inputs).
- Q28–Q30: multiple_choice with 3 options (A, B, C).

 Each answer MUST be UNIQUE — no two questions may test the same fact.
 For matching questions: each item MUST have a DIFFERENT correct letter.
"""

SECTION4_QUESTION_TYPES = """
=== QUESTIONS (Section 4) ===
- Q31–Q35: sentence / table completion (text inputs)
- Q36–Q40: multiple_choice with 3 options (A, B, C).

 EACH ANSWER MUST BE A DIFFERENT FACT 
Each of the 10 questions MUST test a DIFFERENT piece of information.

 BAD — two questions about the same statistic:
    Q31: What percentage of the market did the EU represent in 2023? → "42%"
    Q35: In 2023, which region had the largest market share? → "The EU"

 GOOD — each question targets a distinct fact:
    Q31: What was the recycling rate in 2010? → "18%"
    Q32: What is the projected global market by 2030? → "$88 billion"
    Q33: Which region had the largest share in 2023? → "The EU"
    Q34: What was the AI sorting accuracy in 2018? → "45%"
"""


# ============================================================
# REFERENCE EXAMPLES
# ============================================================

SECTION1_REFERENCE = """
=== REFERENCE STYLE (aim for this naturalness) ===
Agent: Good morning, Riverside Hotel, this is Priya. How can I help?
Customer: Oh, hi. Um, I was hoping to — actually, do you have any rooms
          available next weekend?
Agent: Let me check for you. Just one moment, please... Yes, we do have
       availability. Would that be for Friday and Saturday night?
Customer: Yes, Friday the 15th through Sunday. Well, actually, just Friday
          and Saturday.
Agent: Perfect. How many guests will be staying?
Customer: Just two of us. Oh wait — sorry, three. My sister's joining us.
Agent: No problem, I'll make a note of that. And could I take a contact number?
Customer: Yes, it's 07700 900123... sorry, 900124. I always mix those up.
"""

SECTION2_REFERENCE = """
=== REFERENCE STYLE (aim for this naturalness) ===
Good morning, everyone, and welcome to the museum. My name's Sarah, and
I'll be your guide for the next hour or so. [pause] Now, before we set off,
let me just check everyone can hear me — yes? Great. So, um, a little bit
of history first. This building was originally a Victorian warehouse, and
it was converted into a museum back in 1924.
"""

SECTION3_REFERENCE = """
=== REFERENCE STYLE (aim for this naturalness) ===
Tutor: Right, let's pick up where we left off. James, you were looking at
       the economic angle — what did you find?
James: Well, the numbers are pretty striking. The UK loses something like
       one percent of GDP to sleep-related issues every year.
Tutor: Really? That's a bigger figure than I expected.
Emma: Sorry, can I jump in? I mean, that's a fair point, but I'm not sure
      it's just about money. A lot of people just... don't prioritise sleep.
"""

SECTION4_REFERENCE = """
=== REFERENCE STYLE (aim for this naturalness) ===
Good morning. Today we'll be examining the economics of sleep — a topic
that has, until recently, been surprisingly overlooked. Let me begin with
some context. In 2016, the RAND Corporation published a study suggesting
that sleep deprivation costs the US economy up to 411 billion dollars
annually. That's roughly 2.3 percent of GDP.
"""


# ============================================================
# WORD COUNT TARGETS — ALIGNED with section_generator.SECTION_TARGET_WORDS
# ============================================================

SECTION_WORD_TARGETS = {
    1: "Target length: around 400 words (range 380–450). Focus on quality.",
    2: "Target length: around 450 words (range 420–480). Focus on quality.",
    3: "Target length: around 500 words (range 480–540). Focus on quality.",
    4: "Target length: around 600 words (range 580–650). Focus on quality.",
}


# ============================================================
# SECTION TEMPLATES
# ============================================================

SECTION1_AI_TEMPLATE = """You are an IELTS Listening script writer.

Write Section 1: a natural conversation between a CUSTOMER and an AGENT
about an everyday service (booking a hotel, joining a gym, registering
for a course, renting a car, opening a bank account, etc.).

DIFFICULTY: {difficulty}
ACCENT: {accent}

{reference_example}

=== WHAT THE SCRIPT MUST INCLUDE ===
1. A greeting — Agent answers with company name and their own name.
2. Information exchange — the Agent asks questions and the Customer
   answers naturally, with hesitations and at least 2 self-corrections.
3. A confirmation near the end — Agent repeats back key details.
4. A polite closing.

=== SPELLING (must include 1–2 sequences) ===
The customer spells their FULL NAME, and optionally an email or address.

{natural_speech}

{question_types}

{word_target}

{json_rules}

=== OUTPUT ===
Return ONLY this JSON shape:
{{"script": "Agent: ...\\nCustomer: ...\\n...", "questions": [{{"question": "...", "answer": "...", "type": "form_completion"}}, ...]}}
"""

SECTION2_TEMPLATE = """You are an IELTS Listening script writer.

Write Section 2: a natural MONOLOGUE — a tour guide, information officer,
or museum curator giving a talk about a place.

TOPIC: {topic_title}
DIFFICULTY: {difficulty}

Do NOT give the venue a proper name. Refer to it generically.

{reference_example}

=== WHAT THE SCRIPT MUST INCLUDE ===
1. A welcome — guide introduces themselves and the venue briefly.
2. Practical information woven into the talk, not listed.
3. A description of the layout using MAP LANGUAGE.
4. A clear closing.

=== REQUIRED INFO FOR THIS TOPIC ===
{info_points}

{question_types}

{word_target}

{json_rules}

=== OUTPUT ===
Return ONLY this JSON shape:
{{"script": "Good morning, everyone...", "questions": [{{...}}, ...]}}
"""

SECTION3_TEMPLATE = """You are an IELTS Listening script writer.

Write Section 3: a natural academic discussion between a TUTOR and THREE
STUDENTS about a specific topic.

TOPIC: {topic_title}
DIFFICULTY: {difficulty}

=== SPEAKERS (use these EXACT names and personalities) ===
{speaker_list}

{reference_example}

=== WHAT THE DISCUSSION MUST COVER ===
{key_ideas}

=== STRUCTURE ===
- The tutor opens by framing the topic and inviting one student to start.
- Each student contributes at least twice, with a distinct perspective.
- Students react to each other — agreement, disagreement, building on points.

=== SPEAKER LABELS ===
Use the EXACT names provided above. Every line must start with "Name: ".

{question_types}

{word_target}

{json_rules}

=== OUTPUT ===
Return ONLY this JSON shape:
{{"script": "{first_speaker}: ...\\n...", "questions": [{{...}}, ...]}}
"""

SECTION4_TEMPLATE = """You are an IELTS Listening script writer.

Write Section 4: a natural academic LECTURE delivered by a university
lecturer. This is a single-speaker monologue.

TOPIC: {topic_title}
DIFFICULTY: {difficulty}

{reference_example}

=== WHAT THE LECTURE MUST INCLUDE ===
1. A formal opening — greet, introduce the topic, outline the structure.
2. Background and context.
3. Three main parts with concrete examples and statistics.
4. Applications or implications.
5. Challenges and future direction.
6. A brief conclusion.

=== REFERENCE DATA (weave in naturally, do NOT list) ===
{data_points}

{question_types}

{word_target}

{json_rules}

=== OUTPUT ===
Return ONLY this JSON shape:
{{"script": "Good morning. Today we'll be examining...", "questions": [{{...}}, ...]}}
"""


# ============================================================
# SECTION 2 INFO MAP
# ============================================================

SECTION2_INFO_MAP: Dict[str, str] = {
    "museum": "- Opening hours, ticket prices (adult/student/child/family)\n- Guided tour times and duration\n- Facilities: cafeteria, gift shop, audio guides, restrooms\n- Accessibility: wheelchairs, hearing loops\n- Layout: Main Entrance, Cafeteria, Gift Shop, Restrooms, Exhibition Hall, Lecture Room",
    "zoo": "- Opening hours, ticket prices (adult/child/family/senior)\n- Free guided walk time and duration\n- Animal zones\n- Facilities: buggy rentals, cafeteria, gift shop\n- Layout: Main Gate, African Safari, Asian Enclosure, Australian Walkabout, Reptile House, Bird Aviary, Penguin Pool, Children's Farm",
    "library": "- Opening hours, membership fee\n- Tour times and duration\n- Facilities: cafeteria, computer room, study areas, archives\n- Accessibility: ramps, lifts\n- Layout: Main Entrance, Children's Section, Reference Room, Cafeteria, Archives, Computer Room, Study Area, Reading Room",
    "park": "- Opening hours (seasonal), parking fees\n- Self-guided walk time\n- Facilities: playground, café, boat hire, gardens, lake\n- Accessibility: smooth paths, electric scooters\n- Layout: Main Gate, Visitor Centre, Playground, Lake, Woodland, Formal Gardens, Café, Boat Hire",
    "aquarium": "- Opening hours, ticket prices, tour times\n- Tank zones: Ocean Tunnel, Shark Zone, Coral Reef, Penguin Cove\n- Facilities: cafeteria, gift shop, audio guides\n- Accessibility: wheelchair access, hearing loops\n- Layout: Main Entrance, Ocean Tunnel, Shark Zone, Coral Reef, Penguin Cove, Café, Gift Shop, Restrooms",
    "botanical_garden": "- Opening hours (seasonal), ticket prices\n- Guided tour times\n- Garden zones\n- Facilities: café, gift shop, pond\n- Accessibility: wheelchair-friendly paths\n- Layout: Main Gate, Rose Garden, Tropical House, Japanese Garden, Herb Garden, Café, Gift Shop, Pond",
    "art_gallery": "- Opening hours, ticket prices\n- Tour times and duration\n- Gallery zones\n- Facilities: cafeteria, gift shop, audio guides\n- Layout: Main Entrance, Modern Wing, Renaissance, Sculpture Hall, Photography, Café, Shop, Restrooms",
    "historic_house": "- Opening hours, ticket prices\n- Hourly tours\n- Rooms: Great Hall, Kitchen, Library, Drawing Room, Bedrooms\n- Facilities: café, garden, gift shop\n- Layout: Main Entrance, Great Hall, Kitchen, Library, Drawing Room, Bedrooms, Garden, Gift Shop",
    "theme_park": "- Opening hours, ticket prices\n- Self-guided (full-day visit)\n- Rides and zones\n- Facilities: food outlets, gift shop, lost & found\n- Layout: Main Entrance, Rollercoaster, Water Rides, Kids Zone, Food Court, Gift Shop, Restrooms, Lost & Found",
    "sports_centre": "- Opening hours, day pass prices\n- Tour times and duration\n- Facilities: swimming pool, gym, tennis courts, squash courts, creche\n- Layout: Main Entrance, Swimming Pool, Gym, Tennis Courts, Squash Courts, Café, Changing Rooms, Creche",
    "theatre": "- Box office hours, ticket prices, tour times\n- Facilities: bar, café, cloakroom, hearing loops\n- Layout: Main Entrance, Box Office, Main Auditorium, Studio Theatre, Café, Bar, Cloakroom, Toilets",
    "university_campus": "- Campus opening hours, tour times\n- Facilities: library, lecture theatres, cafeteria, bookshop\n- Layout: Main Gate, Library, Science Building, Arts Building, Sports Hall, Café, Bookshop, Student Union",
}

DEFAULT_S2_INFO = """- Opening hours and ticket prices
- Guided tour times and duration
- Facilities: cafeteria, gift shop, restrooms
- Accessibility information
- Layout with 6+ locations labelled A–F or A–H"""


# ============================================================
# SECTION 2 NUMBERS MAP
# ============================================================

SECTION2_NUMBERS_MAP: Dict[str, str] = {
    "museum": "Established 1924; 2.4M visitors/year; adult $12, student $8, child $6; 45-min tours; parking $5/hr; 6 audio-guide languages; 1,200 exhibits",
    "zoo": "Established 1965; 980k visitors/year; adult $18, child $10; 2-hr tours; free parking; 15% group discount; 380 species; 2,400 animals",
    "library": "Established 1910; 1.5M visitors/year; membership $25/year; 30-min tours; parking $2/hr; 850,000 books; 48 computers; 12 study rooms",
    "park": "Established 1876; 5.2M visitors/year; free entry; parking $3/hr; 120 hectares; 12,000 trees; 4 ponds; 7 gardens; 3 playgrounds",
    "aquarium": "Established 1998; 850k visitors/year; adult $16, child $9; 1.5-hr tours; parking $4/hr; 20% group discount; 450 species; 2.2M-litre tanks",
    "botanical_garden": "Established 1920; 400k visitors/year; adult $8, child $4; 1-hr tours; free parking; 10% group discount; 3,500 plant species",
    "art_gallery": "Established 1932; 1.2M visitors/year; adult $10, student $7; 40-min tours; parking $5/hr; 4 audio-guide languages; 4,800 artworks",
    "historic_house": "Built 1750, opened 1956; 210k visitors/year; adult $14, child $7; 1-hr tours; parking $3/hr; 18 rooms; 25-seat tea room",
    "theme_park": "Established 1986; 1.8M visitors/year; adult $45, child $32; parking $8/day; 20% group discount; 32 rides; 8 food outlets",
    "sports_centre": "Established 2001; 600k visitors/year; day pass $10, student $7; 30-min tours; free parking; 15% group discount; 8 courts; 35 classes/week",
    "theatre": "Established 1892 (refurbished 2005); 380k visitors/year; tickets $25–$60; 1-hr tours; parking $6/hr; 950 seats; 8 productions/year",
    "university_campus": "Established 1967; 14,500 students; 45 hectares; 1.5-hr tours; parking $2/hr; 22 buildings; 5-floor library; 120 societies",
}

DEFAULT_S2_NUMBERS = """- Established year
- Visitors per year
- Adult / student / child ticket prices
- Tour duration in minutes
- Parking fee per hour
- Group discount percentage
- One or two other specific numbers"""


# ============================================================
# SECTION 4 DATA MAP — v4: expanded to cover all 17 topics
# ============================================================

SECTION4_DATA_MAP: Dict[str, str] = {
    "recycling": """- Recycling rate: 18% (2010) → 34% (2023)
- Global market: $56B (2023) → $88B projected (2030)
- Key regions: EU 42%, US 28%, China 15%
- Public support: 64% (2015) → 82% (2023)
- AI sorting accuracy: 72% (up from 45% in 2018)
- Raw material savings: $14.8B (2023)
- Jobs created: 1.2M in recycling sector
- Landfill reduction: 38% decrease (2015–2023)""",
    "climate_change": """- Research funding: +42% (2015–2023)
- Global investment: $1.2T (2023)
- Regional share: N. America + W. Europe + E. Asia = 73%
- Public support: 54% (2015) → 78% (2023)
- Early-warning system accuracy: +22%
- Climate literacy engagement: +35%
- Green sector productivity: +18%
- Projected market value by 2035: $2.8T""",
    "digital_marketing": """- Global growth: 34% YoY (2020–2023)
- Social media ad spend: $255B (2023) → $550B (2030)
- Influencer marketing: $21.1B (2023)
- AI personalisation adoption: 72% of companies
- Mobile share of digital ad spend: 68%
- ROI uplift with data-driven campaigns: +28%
- Businesses using video: 84%
- Email marketing ROI: 42x average""",
    "gardening": """- Urban gardening growth: +46% (2018–2023, UK)
- Community garden projects: 2,800 (2023)
- Garden waste recycled: 6.5M tonnes/year
- Garden-centre revenue: $5.8B (2023)
- UK households with gardens: 86%
- Gardening sector jobs: 82,000
- Nursery species available: 15,000+
- Water reduction with smart systems: 35%""",
    "sleep": """- Economic cost (US): $411B/year (RAND, 2016)
- Share of GDP lost: ~2.3%
- Productivity loss: up to 1% of GDP in UK
- Sleep-deprived adults: 1 in 3
- Recommended adult sleep: 7–9 hours
- Average actual sleep: 6.8 hours
- Teen sleep decline: 8.5 → 7.2 hours (2000–2020)
- Workplace accidents linked to fatigue: +18%""",
    "smart_homes": """- Global smart-home market: $121B (2023) → $340B (2030)
- Household penetration (US): 43% (2023)
- Energy savings with smart thermostats: 10–15%
- IoT devices per household (avg): 21 (2023)
- Voice-assistant adoption: 62% of smart-home owners
- Data-privacy concerns: 71% of users
- Interoperability issues: 3 major standards
- Predicted market by 2035: $500B+""",
    "online_learning": """- Global e-learning market: $250B (2023) → $650B (2030)
- MOOC enrollment: 220M learners (2023)
- Completion rates (MOOCs): 5–15%
- Corporate online training: 78% adoption
- Mobile learning share: 58%
- AI tutor adoption: 34% of platforms
- Cost per online course (avg): $25–$300
- Learner satisfaction: 82% (2023)""",
    "urban_planning": """- Urban population: 56% (2023) → 68% by 2050
- Smart-city investment: $189B (2023)
- Green space per capita target: 9 sq m/person
- Public transport modal share (EU): 27%
- Cycling infrastructure: +38% (2018–2023)
- Traffic congestion cost: $87B/year (US)
- Zoning reform adoption: 42% of major cities
- Affordable housing shortfall: 12M units (EU)""",
    "renewable_energy": """- Global renewables share: 30% (2023)
- Solar capacity: +22% YoY (2020–2023)
- Wind capacity: 900 GW (2023)
- Investment: $495B (2023)
- Cost of solar: −82% since 2010
- Jobs in renewables: 13.7M (2023)
- Storage capacity growth: +48% YoY
- Projected renewables share by 2035: 55%""",
    "artificial_intelligence": """- Global AI market: $207B (2023) → $1.8T (2030)
- Enterprise AI adoption: 35% (2023)
- AI patent filings: +28% YoY
- Compute cost decline: −85% since 2018
- Job displacement projection: 85M roles by 2030
- Job creation projection: 97M new roles
- Data centre energy use: 1–1.5% of global electricity
- Regulatory frameworks: 42 countries drafting policy""",
    "ocean_plastic": """- Ocean plastic: 11M tonnes/year entering oceans
- Projected accumulation: 29M tonnes/year by 2040
- Recycling rate (global plastics): 9%
- Microplastic particles found in human blood: 2022 study
- Cleanup effort: 1.2M tonnes removed by 2023
- Economic damage (marine): $13B/year
- Ban adoption: 60+ countries
- Great Pacific Garbage Patch size: 1.6M km²""",
    "mental_health": """- Global burden: 1 in 8 people live with a mental disorder
- Depression prevalence: 280M (2023)
- Anxiety prevalence: 301M (2023)
- Workplace cost: $1T/year in lost productivity
- Treatment gap: 75% in low-income countries
- Digital mental health market: $27B (2023)
- Youth mental health decline: +32% (2010–2023)
- Global mental health funding: $14B (2023)""",
    "space_exploration": """- Global space economy: $546B (2023)
- Government spending: $117B (2023)
- Private investment: $8.9B (2023)
- Satellite launches: 2,800+ (2023)
- Cost per kg to orbit (Falcon 9): ~$2,700
- Cost reduction since 2010: −84%
- Space debris tracked: 35,000+ objects
- Projected economy by 2035: $1.8T""",
    "nuclear_energy": """- Global nuclear share: 9.2% of electricity (2023)
- Reactors in operation: 440
- Reactors under construction: 60
- Average age of reactors: 31 years
- Investment in SMRs: $8B (2023)
- Waste stockpile: 250,000 tonnes (global)
- Uptime factor: 92%
- Projected share by 2050: 12%""",
    "sustainable_agriculture": """- Global farmland: 4.8B hectares
- Organic farmland: 1.6% of total
- Regenerative farming adoption: +52% (2018–2023)
- Water use: 70% of global freshwater
- Food waste: 1.3B tonnes/year
- Precision-ag market: $12.9B (2023)
- Yield improvement (AI-guided): +18%
- Projected market by 2030: $28B""",
    "economic_inequality": """- Global Gini coefficient: 0.62 (2023)
- Top 1% wealth share: 47.8% (2023)
- Bottom 50% wealth share: 1.2%
- Wealth gap growth: +12% (2019–2023)
- Minimum wage adoption: 90% of countries
- Universal basic income pilots: 45 countries
- Tax avoidance (multinationals): $240B/year
- Projected top 1% share by 2030: 53%""",
    "edtech": """- Global edtech market: $254B (2023)
- Projected market by 2030: $715B
- Mobile learning: 58% of edtech revenue
- AI tutor adoption: 34% of platforms
- VR/AR education market: $9.4B (2023)
- Teacher training investment: +22%
- Digital divide gap: 2.6B people offline
- Student engagement increase (gamified): +31%""",
}

DEFAULT_S4_DATA = """- Research funding growth: +[%] (2015–2023)
- Global investment: $[amount]B
- Key regions: N. America, W. Europe, E. Asia
- Public support: [%] (2015) → [%] (2023)
- Sector productivity gain: +[%]
- Projected market value by 2035: $[amount]B
- At least 2 researcher/study references (with names and years)"""


# ============================================================
# SPEAKER UTILITIES
# ============================================================

def get_speaker_names() -> str:
    """Legacy default. Prefer build_speaker_block() with real names."""
    return "- Tutor (tutor/guide)\n- Student1 (Student 1)\n- Student2 (Student 2)\n- Student3 (Student 3)"


def build_speaker_block(speakers: Optional[List[str]]) -> str:
    """Build the Section 3 speaker block with real names and personalities."""
    if not speakers or len(speakers) < 4:
        return (
            "- Dr. Sarah (Tutor): Warm, uses Socratic method. Says: "
            "\"That's interesting — can you say more?\"\n"
            "- James (Student 1): Data-driven, confident. Says: "
            "\"According to recent research...\"\n"
            "- Emma (Student 2): Thoughtful, hesitant. Says: "
            "\"I'm not sure, but...\"\n"
            "- David (Student 3): Practical. Says: "
            "\"From what I've seen in practice...\""
        )
    tutor, s1, s2, s3 = speakers[0], speakers[1], speakers[2], speakers[3]
    return (
        f"- {tutor} (Tutor): Warm, uses Socratic method. Asks probing questions, "
        f"summarises at the end. Says: \"That's interesting — can you say more?\"\n"
        f"- {s1} (Student 1): Data-driven, confident. Cites research and statistics. "
        f"Says: \"According to a 2022 study...\"\n"
        f"- {s2} (Student 2): Thoughtful, hesitant. Often pushes back politely. "
        f"Says: \"I'm not sure, but...\", \"That's a fair point, however...\"\n"
        f"- {s3} (Student 3): Practical, brings real-world examples. "
        f"Says: \"From what I've seen in practice...\", \"Real-world examples suggest...\""
    )


# ============================================================
# BUILDER FUNCTIONS
# ============================================================

def build_section1_prompt_ai(difficulty: str = "medium",
                             accent: str = "british",
                             target_band: float = 6.5) -> str:
    return SECTION1_AI_TEMPLATE.format(
        difficulty=difficulty,
        accent=accent,
        reference_example=SECTION1_REFERENCE,
        natural_speech=NATURAL_SPEECH_GUIDE,
        question_types=SECTION1_QUESTION_TYPES,
        word_target=SECTION_WORD_TARGETS[1],
        json_rules=STRICT_JSON_BLOCK,
    )


def build_section2_prompt(topic: str, difficulty: str = "medium",
                          target_band: float = 6.5) -> str:
    title = topic.replace("_", " ").title()
    info_points = SECTION2_INFO_MAP.get(topic, DEFAULT_S2_INFO)
    numbers_section = SECTION2_NUMBERS_MAP.get(topic, DEFAULT_S2_NUMBERS)
    combined_info = f"{info_points}\n\nNumbers to weave in:\n{numbers_section}"
    return SECTION2_TEMPLATE.format(
        topic_title=title,
        difficulty=difficulty,
        reference_example=SECTION2_REFERENCE,
        info_points=combined_info,
        question_types=SECTION2_QUESTION_TYPES,
        word_target=SECTION_WORD_TARGETS[2],
        json_rules=STRICT_JSON_BLOCK,
    )


def build_section3_prompt(topic: str, difficulty: str = "medium",
                          target_band: float = 6.5,
                          custom_speakers: Optional[List[str]] = None) -> str:
    title = topic.replace("_", " ").title()
    speaker_block = build_speaker_block(custom_speakers)
    key_ideas = (
        "- The economic angle — what does this cost, who pays, what's at stake\n"
        "- The social or health dimension — who is affected and how\n"
        "- Policy or practical responses — what could change\n"
        "- The future — where this is heading in the next decade"
    )
    first_speaker = custom_speakers[0] if custom_speakers else "Dr. Sarah"
    return SECTION3_TEMPLATE.format(
        topic_title=title,
        difficulty=difficulty,
        speaker_list=speaker_block,
        reference_example=SECTION3_REFERENCE,
        key_ideas=key_ideas,
        question_types=SECTION3_QUESTION_TYPES,
        word_target=SECTION_WORD_TARGETS[3],
        json_rules=STRICT_JSON_BLOCK,
        first_speaker=first_speaker,
    )


def build_section4_prompt(topic: str, difficulty: str = "medium",
                          target_band: float = 6.5) -> str:
    title = topic.replace("_", " ").title()
    data_points = SECTION4_DATA_MAP.get(topic, DEFAULT_S4_DATA)
    return SECTION4_TEMPLATE.format(
        topic_title=title,
        difficulty=difficulty,
        reference_example=SECTION4_REFERENCE,
        data_points=data_points,
        question_types=SECTION4_QUESTION_TYPES,
        word_target=SECTION_WORD_TARGETS[4],
        json_rules=STRICT_JSON_BLOCK,
    )


# ============================================================
# TOPIC UTILITIES
# ============================================================

def get_section2_topics() -> List[str]:
    return [
        "museum", "zoo", "library", "park", "aquarium", "botanical_garden",
        "art_gallery", "historic_house", "theme_park", "sports_centre",
        "theatre", "university_campus",
    ]


def get_section4_topics() -> List[str]:
    # v4: this list is now fully covered by SECTION4_DATA_MAP
    return [
        "climate_change", "urban_planning", "renewable_energy",
        "artificial_intelligence", "ocean_plastic", "mental_health",
        "space_exploration", "nuclear_energy", "sustainable_agriculture",
        "economic_inequality", "edtech", "recycling", "digital_marketing",
        "gardening", "sleep", "smart_homes", "online_learning",
    ]


def get_all_topics() -> Dict[str, List[str]]:
    return {
        "section2": get_section2_topics(),
        "section4": get_section4_topics(),
    }


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "STRICT_JSON_BLOCK",
    "NATURAL_SPEECH_GUIDE",
    "SECTION1_QUESTION_TYPES",
    "SECTION2_QUESTION_TYPES",
    "SECTION3_QUESTION_TYPES",
    "SECTION4_QUESTION_TYPES",
    "SECTION1_REFERENCE",
    "SECTION2_REFERENCE",
    "SECTION3_REFERENCE",
    "SECTION4_REFERENCE",
    "SECTION2_INFO_MAP",
    "SECTION2_NUMBERS_MAP",
    "SECTION4_DATA_MAP",
    "DEFAULT_S2_INFO",
    "DEFAULT_S2_NUMBERS",
    "DEFAULT_S4_DATA",
    "build_section1_prompt_ai",
    "build_section2_prompt",
    "build_section3_prompt",
    "build_section4_prompt",
    "get_speaker_names",
    "build_speaker_block",
    "get_section2_topics",
    "get_section4_topics",
    "get_all_topics",
]