# modules/ielts/writing/counter_argument.py
"""Generate and evaluate counter-arguments for Task 2 essays - PURE AI, NO FALLBACKS"""

import re
from typing import Dict, List, Optional


class CounterArgumentGenerator:
    """Identify missing counter-arguments and generate them using AI"""
    
    def __init__(self, ai_engine=None):
        if not ai_engine:
            raise ValueError(" AI Engine is required for CounterArgumentGenerator. No fallback templates available.")
        
        self.ai = ai_engine
        print(f"[CounterArgumentGenerator] Initialized with pure AI mode")
    
    def analyze(self, essay: str, topic: str, position: str = None) -> Dict:
        """Analyze essay for counter-arguments - PURE ANALYSIS"""
        
        if not essay or len(essay.strip()) < 100:
            return {
                'has_counter_argument': False,
                'counter_keywords_found': [],
                'counter_quality': 'insufficient_text',
                'counter_score': 0,
                'missing_arguments': ['Essay too short for analysis'],
                'generated_counter_argument': None,
                'suggestions': ['Write a longer essay (min 250 words) for proper analysis']
            }
        
        essay_lower = essay.lower()
        
        # Keywords that indicate counter-arguments (detection only)
        counter_keywords = [
            'however', 'although', 'while it is true', 'admittedly', 
            'despite', 'nevertheless', 'nonetheless', 'on the other hand',
            'some may argue', 'critics argue', 'opponents claim', 'conversely',
            'granted', 'even though', 'in spite of'
        ]
        
        # Detect existing counter-arguments
        found_keywords = []
        for keyword in counter_keywords:
            if keyword in essay_lower:
                found_keywords.append(keyword)
        
        has_counter_argument = len(found_keywords) > 0
        
        # Score the counter-argument strength based on detection
        if has_counter_argument:
            # Check if counter-argument is well-developed
            sentences = [s.strip() for s in essay.split('.') if len(s.strip()) > 10]
            counter_sentences = []
            for s in sentences:
                if any(kw in s.lower() for kw in counter_keywords):
                    counter_sentences.append(s)
            
            # Assess quality based on length of counter-argument
            if len(counter_sentences) >= 2:
                counter_quality = 'good'
                counter_score = 75
            elif len(counter_sentences) == 1:
                # Check if the single sentence is substantial
                if len(counter_sentences[0].split()) > 15:
                    counter_quality = 'adequate'
                    counter_score = 60
                else:
                    counter_quality = 'basic'
                    counter_score = 45
            else:
                counter_quality = 'basic'
                counter_score = 40
        else:
            counter_quality = 'missing'
            counter_score = 0
        
        # Generate missing counter-arguments using AI (NOT template)
        generated_ca = None
        if not has_counter_argument:
            try:
                generated_ca = self._generate_counter_argument(essay, topic, position)
            except Exception as e:
                print(f"[CounterArgument] Generation failed: {e}")
                generated_ca = None
        
        return {
            'has_counter_argument': has_counter_argument,
            'counter_keywords_found': found_keywords,
            'counter_quality': counter_quality,
            'counter_score': counter_score,
            'missing_arguments': self._identify_missing_arguments(essay, topic),
            'generated_counter_argument': generated_ca,
            'suggestions': self._get_suggestions(has_counter_argument, counter_quality),
            'analyzer': 'rule-based-detection',
            'generator': 'ai' if generated_ca else 'none'
        }
    
    def _generate_counter_argument(self, essay: str, topic: str, position: str) -> str:
        """Generate a counter-argument using AI - NO TEMPLATES"""
        if not self.ai:
            raise RuntimeError(" Cannot generate counter-argument: AI Engine not available")
        
        # Truncate essay if too long
        truncated_essay = essay[:1200] if len(essay) > 1200 else essay
        
        prompt = f"""You are an IELTS writing expert. Generate a strong counter-argument for this essay.

═══════════════════════════════════════════════════════════════
TOPIC: {topic[:200]}
POSITION: {position if position else 'Not specified - generate balanced counter-argument'}
═══════════════════════════════════════════════════════════════

ESSAY EXCERPT:
{truncated_essay}

═══════════════════════════════════════════════════════════════
REQUIREMENTS:
- 2-3 sentences
- Acknowledge the opposing view
- Refute it with logical reasoning
- Use appropriate linking words (while, although, admittedly, however)
- Return ONLY the counter-argument text, no explanations
═══════════════════════════════════════════════════════════════

Generate a NATURAL, ORIGINAL counter-argument:"""
        
        response = self.ai.generate(prompt, max_tokens=200, temperature=0.7)
        
        if not response or len(response.strip()) < 20:
            raise ValueError("AI generated insufficient counter-argument")
        
        return response.strip()
    
    def _identify_missing_arguments(self, essay: str, topic: str) -> List[str]:
        """Identify what counter-arguments are missing - ANALYSIS ONLY"""
        missing = []
        essay_lower = essay.lower()
        
        # Check for concession indicators
        concession_words = ['although', 'while', 'despite', 'even though', 'admittedly']
        has_concession = any(word in essay_lower for word in concession_words)
        
        if not has_concession:
            missing.append("No concession to opposing viewpoint - acknowledge other perspectives")
        
        # Check for refutation indicators
        refutation_words = ['however', 'nevertheless', 'nonetheless', 'yet', 'but']
        has_refutation = any(word in essay_lower for word in refutation_words)
        
        if not has_refutation:
            missing.append("No refutation - explain why your position is stronger")
        
        # Check essay length
        word_count = len(essay.split())
        if word_count < 250:
            missing.append(f"Essay is short ({word_count}/250 words) - develop counter-arguments more fully")
        
        # Check if only one perspective is presented
        perspective_words = ['some people', 'many believe', 'proponents', 'supporters']
        opposing_perspective = ['others argue', 'opponents', 'critics', 'on the other hand']
        
        has_perspective = any(word in essay_lower for word in perspective_words)
        has_opposing = any(word in essay_lower for word in opposing_perspective)
        
        if has_perspective and not has_opposing:
            missing.append("Only presented one perspective - discuss both sides for higher band")
        
        return missing
    
    def _get_suggestions(self, has_ca: bool, quality: str) -> List[str]:
        """Get improvement suggestions - GUIDANCE ONLY"""
        suggestions = []
        
        if not has_ca:
            suggestions.append(" Add a counter-argument paragraph acknowledging the opposing view")
            suggestions.append(" Use: 'While it is true that...', 'Admittedly...', or 'Although some may argue...'")
            suggestions.append(" After presenting opposing view, explain why your position is stronger")
        elif quality == 'basic':
            suggestions.append(" Develop your counter-argument more fully with specific reasoning")
            suggestions.append(" Add a refutation sentence starting with 'However,' or 'Nevertheless,'")
            suggestions.append(" Provide a concrete example to strengthen your refutation")
        elif quality == 'adequate':
            suggestions.append(" Add a second counter-point to address multiple opposing arguments")
            suggestions.append(" Use more sophisticated concession language: 'Granted...', 'While it may be true...'")
        else: # good
            suggestions.append(" Strong counter-argument. For Band 8+, add a nuanced concession")
            suggestions.append(" Consider addressing the most compelling opposing argument first")
        
        return suggestions
    
    def generate_full_counter_paragraph(self, essay: str, topic: str, position: str) -> str:
        """Generate a complete counter-argument paragraph - PURE AI"""
        if not self.ai:
            raise RuntimeError(" Cannot generate paragraph: AI Engine not available")
        
        truncated_essay = essay[:1000] if len(essay) > 1000 else essay
        
        prompt = f"""Write a complete counter-argument paragraph (4-5 sentences) for this IELTS essay.

TOPIC: {topic[:200]}
MAIN POSITION: {position if position else 'Based on the essay'}

Essay context: {truncated_essay}

STRUCTURE:
1. Concession sentence (acknowledge opposing view)
2. Example/explanation of opposing view
3. Refutation transition (However, Nevertheless)
4. Rebuttal with reasoning
5. Return to your main argument

Return ONLY the paragraph, no explanations."""
        
        response = self.ai.generate(prompt, max_tokens=300, temperature=0.7)
        
        if not response or len(response.strip()) < 50:
            raise ValueError("AI generated insufficient paragraph")
        
        return response.strip()


# Factory function
def create_counter_argument_generator(ai_engine):
    """Factory function to create CounterArgumentGenerator with AI engine"""
    if not ai_engine:
        raise ValueError(" AI Engine required to create CounterArgumentGenerator")
    
    return CounterArgumentGenerator(ai_engine)


# Do NOT create instance here - will be created by app
# counter_arg = CounterArgumentGenerator() # REMOVED