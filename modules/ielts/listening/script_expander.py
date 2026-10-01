"""Advanced IELTS Listening script expander with distractor integration and prompt_templates data"""

import random
import logging
from typing import Dict, Any, List, Optional

# FIXED IMPORT – from distractor_generator (correct module name)
try:
    from .distractor_rules import distractor_generator
except ImportError:
    distractor_generator = None
    logging.warning("distractor_generator not available")

# Import data maps from prompt_templates for realistic fallback
try:
    from .prompt_templates import (
        SECTION2_INFO_MAP,
        SECTION2_NUMBERS_MAP,
        SECTION4_DATA_MAP,
    )
except ImportError:
    SECTION2_INFO_MAP = {}
    SECTION2_NUMBERS_MAP = {}
    SECTION4_DATA_MAP = {}
    logging.warning("prompt_templates data maps not available")

logger = logging.getLogger(__name__)


class ScriptExpander:
    """Expand short templates into realistic IELTS scripts with distractors."""

    GREETINGS = [
        "Good morning, how can I help you today?",
        "Hello, thank you for calling. What can I do for you?",
        "Good afternoon, you've reached {business}. How may I assist you?",
        "Hi there, thanks for calling {business}. How can I help?",
    ]

    HESITATIONS = ["um", "let me see", "just a moment", "well", "actually", "hmm", "I think"]

    SMALL_TALK = [
        "I hope you're having a good day so far.",
        "The weather has been lovely recently, hasn't it?",
        "Have you used our service before?",
        "Is this your first time contacting us?",
        "I trust you're keeping well?",
    ]

    CLARIFICATIONS = [
        "Sorry, could you repeat that please?",
        "Let me confirm that.",
        "Could you spell that for me?",
        "I didn't quite catch that.",
        "Just to double-check...",
        "So just to clarify...",
        "Let me read that back to you.",
    ]

    AGENT_ASK_PATTERNS = [
        "Could I have your {field} please?",
        "May I take your {field}?",
        "I'll need your {field} for the records.",
        "Could you tell me your {field}?",
        "Let me take your {field}.",
        "And the {field} would be?",
        "I just need to confirm your {field}.",
    ]

    CUSTOMER_RESPONSE_PATTERNS = [
        "It's {answer}.",
        "That would be {answer}.",
        "The {field} is {answer}.",
        "Sure, it's {answer}.",
        "Of course, {answer}.",
        "Yes, it's {answer}.",
        "I think it's {answer}. Actually, let me check... yes, {answer}.",
    ]

    # ============================================================
    # SECTION 1 EXPANSION (with distractors)
    # ============================================================

    def expand_section1(
        self,
        scenario: Dict[str, Any],
        target_words: int = 520,
        accent: str = "british"
    ) -> str:
        """Expand Section 1 with distractors and realistic conversation."""
        speaker_a = scenario.get("speaker_a", "Customer")
        speaker_b = scenario.get("speaker_b", "Agent")
        fields = scenario.get("fields", [])[:10]
        business = scenario.get("title", "our service")

        # Generate field distractors with section and topic
        distractor_map = {}
        if distractor_generator: # Guard – only call if available
            distractor_map = distractor_generator.generate_distractors_for_fields(
                fields=fields,
                num_distractors=min(6, len(fields)),
                section=1,
                topic=scenario.get("title")
            )

        lines = []
        # Opening
        lines.append(f"{speaker_b}: {random.choice(self.GREETINGS).format(business=business)}")
        lines.append(f"{speaker_a}: Hello, {random.choice(self.HESITATIONS)}, I'd like some information about {business.lower()} please.")

        if random.random() < 0.4:
            lines.append(f"{speaker_b}: {random.choice(self.SMALL_TALK)}")
            lines.append(f"{speaker_a}: Yes, actually this is my first time, {random.choice(self.HESITATIONS)}.")

        # Main information exchange
        for i, field in enumerate(fields):
            q = self._safe_str(field.get("q", "information"))
            a = self._safe_str(field.get("a", "N/A"))

            # Agent asks question with varied pattern
            ask_pattern = random.choice(self.AGENT_ASK_PATTERNS)
            lines.append(f"{speaker_b}: {ask_pattern.format(field=q.lower())}")

            # Customer response with hesitation
            resp_pattern = random.choice(self.CUSTOMER_RESPONSE_PATTERNS)
            lines.append(f"{speaker_a}: {resp_pattern.format(field=q.lower(), answer=a)}")

            # Insert distractor if available for this field
            if i in distractor_map:
                distractor = distractor_map[i]
                lines.append(f"{speaker_b}: Let me confirm that...")
                lines.append(f"{speaker_a}: {distractor}")
                lines.append(f"{speaker_b}: {random.choice(self.CLARIFICATIONS)}")
                lines.append(f"{speaker_a}: Yes, that's correct.")

            # Periodic clarification
            if i > 0 and i % 3 == 0 and random.random() < 0.4:
                lines.append(f"{speaker_b}: {random.choice(self.CLARIFICATIONS)}")
                lines.append(f"{speaker_a}: Yes, that's correct.")

            # Spelling sequence for name fields (1-2 per conversation)
            if i == 2 and field.get("t") == "name" and len(a) > 3:
                lines.append(f"{speaker_b}: Could you spell that for me please?")
                spelled = " - ".join(list(a[:8].upper()))
                lines.append(f"{speaker_a}: Sure. It's {spelled}.")
                lines.append(f"{speaker_b}: Thank you, {a}?")
                lines.append(f"{speaker_a}: That's right.")
            elif i == 5 and field.get("t") == "name" and len(a) > 3 and random.random() < 0.5:
                # Second spelling sequence if another name field exists
                lines.append(f"{speaker_b}: Could you spell that one more time?")
                spelled = " - ".join(list(a[:8].upper()))
                lines.append(f"{speaker_a}: Of course, it's {spelled}.")
                lines.append(f"{speaker_b}: Got it, {a}.")
                lines.append(f"{speaker_a}: Yes, that's correct.")

            # Natural pause between exchanges
            if i < len(fields) - 1 and random.random() < 0.2:
                lines.append(f"{speaker_b}: {random.choice(['Okay, great.', 'Alright, thank you.', 'Let me note that down.'])}")

        # Closing
        lines.append(f"{speaker_b}: And could I just ask, how did you hear about us?")
        lines.append(f"{speaker_a}: Oh, {random.choice(self.HESITATIONS)}, a friend recommended your service actually.")
        lines.append(f"{speaker_b}: That's lovely to hear. We always appreciate referrals.")
        lines.append(f"{speaker_b}: Is there anything else I can help you with today?")
        lines.append(f"{speaker_a}: No, that's everything. Thank you very much for your help.")
        lines.append(f"{speaker_b}: You're very welcome. Have a wonderful day. Goodbye.")
        lines.append(f"{speaker_a}: Goodbye.")

        script = "\n".join(lines)
        return self._smart_pad(script, target_words)

    # ============================================================
    # SECTION 2 EXPANSION (with prompt_templates data)
    # ============================================================

    def expand_section2(
        self,
        topic: str,
        target_words: int = 650,
        accent: str = "british"
    ) -> str:
        """Expand Section 2 using prompt_templates data for realistic content."""
        title = topic.replace("_", " ").title()

        # Get data from prompt_templates or use fallback
        info_data = SECTION2_INFO_MAP.get(topic, "")
        numbers_data = SECTION2_NUMBERS_MAP.get(topic, "")

        # Parse info points into structured data
        info_dict = {}
        if info_data:
            for line in info_data.split('\n'):
                line = line.strip()
                if ': ' in line:
                    key, value = line.split(': ', 1)
                    info_dict[key.strip()] = value.strip()

        # Parse numbers into structured data
        numbers_dict = {}
        if numbers_data:
            for line in numbers_data.split('\n'):
                line = line.strip()
                if ': ' in line:
                    key, value = line.split(': ', 1)
                    numbers_dict[key.strip()] = value.strip()

        # Build paragraph components with variation
        year = numbers_dict.get('Year established', '1980')
        visitors = numbers_dict.get('Total visitors', '1.5 million')
        adult = numbers_dict.get('Adult price', '$12')
        student = numbers_dict.get('Student price', '$8')
        parking = numbers_dict.get('Parking fee', '$5 per hour')
        languages = numbers_dict.get('Audio guide languages', '6')
        sections = info_dict.get('Map', 'Main sections with labels A-H')
        tour_duration = numbers_dict.get('Tour duration', '45 minutes')

        # Random variation for numbers
        if isinstance(adult, str) and adult.startswith('$'):
            try:
                base = int(adult.replace('$', '').replace(',', ''))
                adult = f"${base + random.choice([-2, -1, 0, 1, 2])}"
            except:
                pass

        paragraphs = [
            f"Good morning everyone and welcome to {title}. "
            f"My name is Sarah and I'll be your guide today. "
            f"Before we begin the tour, I'd like to share some important information "
            f"about our facility and what you can expect during your visit.",

            f"{title} was first established in {year} and has since become one of the "
            f"most popular attractions in the region. Over the years, we have welcomed "
            f"more than {visitors} visitors from around the world. "
            f"The building itself covers a substantial area and is divided into "
            f"several main sections. {sections}",

            f"Our tours normally last around {tour_duration}, although many visitors "
            f"choose to stay much longer to explore at their own pace. "
            f"I'd recommend allowing at least two hours for a full visit.",

            f"Now, a few important rules. Photography is allowed in most areas, "
            f"but please make sure your flash is turned off as it can damage "
            f"some of the more sensitive exhibits. Food and drinks are not "
            f"permitted inside the main exhibition rooms. There is a cafeteria "
            f"on the ground floor if you need refreshments.",

            f"As for admission, adult tickets cost {adult}, while students and seniors "
            f"pay {student}. Children under twelve enter free of charge. "
            f"Parking is available at {parking}, with spaces for over 200 cars. "
            f"Alternatively, public transport is very convenient — the number 42 bus "
            f"stops right outside, and the nearest train station is just a ten-minute walk.",

            f"Audio guides are available in {languages} different languages and can be "
            f"collected from the information desk near the main entrance. "
            f"They provide fascinating details about our exhibits and are highly recommended.",

            f"If you need any assistance during your visit, staff members are available "
            f"throughout the building. There are also accessible facilities for "
            f"visitors with mobility requirements on every floor.",

            f"Thank you for listening, and I hope you enjoy your visit to {title}. "
            f"Now, if you'd like to follow me, we'll begin our tour.",
        ]

        script = " ".join(paragraphs)
        return self._smart_pad(script, target_words)

    # ============================================================
    # SECTION 3 EXPANSION (with topic-specific discussion)
    # ============================================================

    def expand_section3(
        self,
        topic: str,
        target_words: int = 650,
        accent: str = "british",
        speakers: Optional[List[str]] = None
    ) -> str:
        """Expand Section 3 with dynamic speakers and topic-specific content."""
        title = topic.replace("_", " ").title()

        if not speakers:
            speakers = ["Professor", "Maria", "James", "Sarah"]

        tutor, s1, s2, s3 = speakers[:4] if len(speakers) >= 4 else speakers + ["Student"] * (4 - len(speakers))

        # Topic-specific discussion points
        discussion_points = {
            "education": {
                "s1": "impact of digital technology on student engagement and learning outcomes",
                "s2": "concerns about the digital divide and equitable access to technology",
                "s3": "teacher training and professional development in the digital age",
            },
            "technology": {
                "s1": "economic benefits and innovation driven by technological advancement",
                "s2": "ethical considerations and privacy concerns with AI and data collection",
                "s3": "environmental impact of technology and e-waste management",
            },
            "environment": {
                "s1": "policy measures needed to address climate change effectively",
                "s2": "economic costs and benefits of transitioning to renewable energy",
                "s3": "role of individual behaviour and lifestyle changes in environmental protection",
            },
            "health": {
                "s1": "advances in medical technology and treatment options",
                "s2": "public health policies and preventative medicine programmes",
                "s3": "mental health awareness and support services",
            },
            "business": {
                "s1": "impact of globalisation on small and medium enterprises",
                "s2": "challenges of digital transformation and e-commerce",
                "s3": "corporate social responsibility and sustainability initiatives",
            },
            "sports": {
                "s1": "economic impact of major sporting events on host cities",
                "s2": "health benefits and community engagement through sports",
                "s3": "issues of doping, ethics, and fairness in competitive sports",
            },
            "travel": {
                "s1": "sustainable tourism practices and eco-friendly travel options",
                "s2": "economic dependence on tourism in developing regions",
                "s3": "preservation of cultural heritage in the age of mass tourism",
            },
            "culture": {
                "s1": "preservation of traditional art forms in the modern world",
                "s2": "cultural exchange programmes and international understanding",
                "s3": "funding challenges for cultural institutions and museums",
            },
        }

        # Default if topic not found
        points = discussion_points.get(topic, {
            "s1": f"key developments and trends in {title}",
            "s2": f"challenges and obstacles facing the {title} sector",
            "s3": f"future prospects and recommendations for {title}",
        })

        lines = [
            f"{tutor}: Good morning everyone. Today we're going to discuss {title} "
            f"and its significance in our modern world. This topic has generated "
            f"considerable debate and research in recent years. {s1}, could you "
            f"share your thoughts on this subject?",

            f"{s1}: Thank you, {tutor}. I think {title} is extremely important, particularly "
            f"when we consider its impact on everyday life and society as a whole. "
            f"One of the key areas is {points['s1']}. This has been a "
            f"major focus of research over the past decade, and the findings "
            f"are quite compelling when we look at the evidence.",

            f"{s2}: I agree with {s1} to some extent, but I also think we need to consider "
            f"the other side of the argument. {points['s2']} is something "
            f"that we cannot afford to overlook. Recent studies have highlighted "
            f"several important factors that challenge the conventional view on this matter.",

            f"{tutor}: That's a very interesting perspective, {s2}. Could you provide "
            f"some specific examples to illustrate your point?",

            f"{s2}: Certainly, {tutor}. For instance, when we look at the data from "
            f"the past five years, we can see a clear pattern emerging. There are "
            f"several case studies that demonstrate exactly what I'm describing. "
            f"I think it's crucial that we examine these in more detail.",

            f"{s3}: I'd like to add another dimension to this discussion. "
            f"{points['s3']} is an issue that I feel hasn't been given "
            f"enough attention in mainstream discourse. We need to think more carefully "
            f"about the implications for communities and individuals on the ground.",

            f"{tutor}: Excellent point, {s3}. {s1}, what are your thoughts on what "
            f"{s3} has just raised?",

            f"{s1}: I think {s3} makes a very valid point. In fact, I would go further "
            f"and argue that we need to incorporate these considerations into our "
            f"overall assessment of the situation. It's not just about the numbers — "
            f"there are human and social factors that are equally important to consider.",

            f"{s2}: I'd like to respond to that. While I agree that we shouldn't "
            f"ignore these human factors, I also think we need to be realistic about "
            f"the constraints and challenges involved. There's no easy solution, "
            f"but that doesn't mean we shouldn't try to find one.",

            f"{tutor}: This has been a very productive discussion. It's clear that "
            f"{title} is a complex subject that requires a multi-faceted approach. "
            f"We've heard different perspectives today, and each one has its merits. "
            f"Next week, we'll continue this conversation and explore some of the "
            f"solutions and strategies that have been proposed. Please come prepared "
            f"with your research and ideas.",
        ]

        script = "\n\n".join(lines)
        return self._smart_pad(script, target_words)

    # ============================================================
    # SECTION 4 EXPANSION (with prompt_templates data)
    # ============================================================

    def expand_section4(
        self,
        topic: str,
        target_words: int = 650,
        accent: str = "british"
    ) -> str:
        """Expand Section 4 using prompt_templates data for realistic lecture content."""
        title = topic.replace("_", " ").title()

        # Get data from prompt_templates
        data = SECTION4_DATA_MAP.get(topic, "")
        data_points = {}

        if data:
            for line in data.split('\n'):
                line = line.strip()
                if ': ' in line:
                    key, value = line.split(': ', 1)
                    data_points[key.strip()] = value.strip()

        # Extract specific data points with variation
        funding = data_points.get('Research funding increase', '42%')
        if '-' in funding:
            funding = funding.split('-')[0].strip()

        investment = data_points.get('Total global investment', '$1.2 trillion')
        support = data_points.get('Public opinion', '78%')
        if '->' in support:
            support = support.split('->')[1].strip()

        healthcare = data_points.get('Healthcare improvement', '22%')
        education = data_points.get('Education gains', '35%')
        productivity = data_points.get('Business productivity', '18%')
        projected = data_points.get('Projected value by 2035', '$2.8 trillion')
        factors = data_points.get('Key factors', '3')
        approaches = data_points.get('Approaches', '3')

        paragraphs = [
            f"Good morning everyone. In today's lecture, we'll examine the topic "
            f"of {title} in some depth. This is a field that has changed dramatically "
            f"over the past two decades, and I want to share with you some of the "
            f"most significant developments and findings that have emerged from "
            f"research conducted around the world.",

            f"To begin with some historical context, researchers first began serious "
            f"investigation into this area during the early 1990s. At that time, "
            f"the tools and methods available were relatively limited, which "
            f"restricted the scope of early studies. However, pioneering work by "
            f"several research groups laid the foundation for what followed.",

            f"Moving on to the key factors that have influenced development in this "
            f"field, I've identified {factors} main areas. The first is technological "
            f"advancement. New tools and methodologies have allowed researchers to "
            f"gather and analyse data that was previously inaccessible. This has "
            f"fundamentally changed our understanding of the subject and opened up "
            f"new avenues for investigation.",

            f"The second major factor is government policy and investment. Research "
            f"funding in this area increased by approximately {funding} between 2010 and 2023, "
            f"reaching a total of around {investment} globally. "
            f"Countries that invested early have seen the greatest returns in terms "
            f"of scientific output and practical applications. This demonstrates "
            f"the importance of sustained funding for long-term research.",

            f"The third factor involves changing social attitudes and growing public "
            f"awareness. Surveys show that public understanding of {title} has "
            f"improved significantly, with positive perceptions rising to nearly "
            f"{support} in 2023. This shift has created a more favourable environment "
            f"for research and innovation, and has encouraged greater participation "
            f"from diverse stakeholders.",

            f"In terms of practical applications, the impact can be seen across "
            f"multiple sectors. In healthcare, improvements have increased diagnostic "
            f"accuracy by approximately {healthcare} percent. Educational institutions "
            f"have reported significantly higher student engagement through new "
            f"learning technologies, with gains of around {education} percent. "
            f"Businesses have seen productivity gains averaging between {productivity} percent.",

            f"Looking ahead, there are several challenges that need to be addressed. "
            f"These include ethical considerations around data privacy, the risk of "
            f"widening inequality between those with access to new technologies and "
            f"those without, and the environmental costs of rapid technological change. "
            f"However, experts predict this field could generate up to {projected} "
            f"in economic value by 2035.",

            f"To conclude, {title} remains one of the most dynamic and rapidly "
            f"developing areas of modern research. The challenges ahead are significant, "
            f"but so too are the opportunities. In next week's lecture, we'll explore "
            f"each of these themes in greater detail, examining specific case studies "
            f"and their implications. Thank you for your attention, and I'm happy "
            f"to take any questions you might have.",
        ]

        script = " ".join(paragraphs)
        return self._smart_pad(script, target_words)

    # ============================================================
    # HELPERS
    # ============================================================

    @staticmethod
    def _safe_str(value: Any) -> str:
        """Safely convert any value to string."""
        if value is None:
            return "N/A"
        if isinstance(value, (list, tuple)):
            return random.choice(value) if value else "N/A"
        if isinstance(value, dict):
            return str(value.get('a', value.get('answer', 'N/A')))
        return str(value)

    def _smart_pad(self, text: str, target_words: int) -> str:
        """Pad script to target word count with natural additions while preserving speaker labels."""
        words = text.split()
        if len(words) >= target_words:
            return text

        natural_additions = [
            " Could you just confirm that for me?",
            " Let me just check that in our system.",
            " I see, and is there anything else?",
            " That's very helpful, thank you.",
            " Just to be sure, could you repeat that?",
            " Perfect, I've noted that down.",
            " Thank you for that information.",
            " That's excellent, thank you.",
            " Let me just update our records.",
            " I think that covers everything.",
        ]

        lines = text.split('\n')
        words_so_far = len(" ".join(lines).split())

        if len(lines) < 3:
            # If too few lines, just add more content at the end
            while words_so_far < target_words:
                if lines and ':' in lines[-1]:
                    speaker = lines[-1].split(':')[0]
                    lines.append(f"{speaker}: {random.choice(natural_additions).strip()}")
                else:
                    lines.append(random.choice(natural_additions).strip())
                words_so_far = len(" ".join(lines).split())
            return "\n".join(lines)

        # Insert additions naturally at random positions
        attempts = 0
        while words_so_far < target_words and attempts < 50:
            attempts += 1
            # Find a line with a speaker label
            speaker_lines = [i for i, line in enumerate(lines) if ':' in line and len(line) > 10]
            if not speaker_lines:
                break
            target_line = random.choice(speaker_lines)
            addition = random.choice(natural_additions)
            # Insert the addition as a new line after the target line
            if target_line + 1 < len(lines):
                lines.insert(target_line + 1, addition.strip())
            else:
                lines.append(addition.strip())
            words_so_far = len(" ".join(lines).split())

        return "\n".join(lines)


# ============================================================
# SINGLETON
# ============================================================

script_expander = ScriptExpander()