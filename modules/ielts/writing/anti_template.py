# modules/ielts/writing/anti_template.py
"""Detect memorized templates and clichéd phrases in IELTS essays

This module performs ANALYSIS only - it does NOT generate content or use templates.
It detects memorized phrases to help identify template-based essays.
"""

import re
from typing import Dict, List, Optional


class AntiTemplateDetector:
    """Detect template phrases, memorized chunks, and overused expressions
    
    This is an ANALYSIS tool, not a content generator.
    It helps identify when students are using memorized templates.
    """
    
    # Common IELTS memorized phrases - used for DETECTION only
    TEMPLATE_PHRASES = {
        'high_risk': [
            (r'in this day and age', 'Overused cliché - be more direct'),
            (r'in today\'s modern society', 'Template phrase - be specific about which society'),
            (r'it is a widely held belief that', 'Memorized opener - state the belief directly'),
            (r'there is no denying that', 'Template phrase - just state the fact'),
            (r'it goes without saying that', 'Overused expression - remove or rephrase'),
            (r'last but not least', 'Cliché - use "finally" instead'),
            (r'every coin has two sides', 'Overused metaphor - describe the specific sides'),
            (r'where there is a will there is a way', 'Cliché - use original phrasing'),
            (r'it has both advantages and disadvantages', 'Template phrase - be specific about what they are'),
            (r'on the one hand.*on the other hand', 'Overused structure - vary your transitions'),
        ],
        'medium_risk': [
            (r'plays an increasingly important role', 'Common phrase - can be more specific'),
            (r'with the development of', 'Template opener - specify what development'),
            (r'as far as i am concerned', 'Overused personal opinion - "I believe" is fine'),
            (r'from my perspective', 'Common phrase - acceptable in moderation'),
            (r'it is my firm belief that', 'Template opinion - just say "I believe"'),
        ],
        'low_risk': [
            (r'in conclusion', 'Standard - fine when used appropriately'),
            (r'to summarize', 'Standard - fine when used appropriately'),
            (r'firstly', 'Standard - fine for listing points'),
            (r'furthermore', 'Standard - fine in moderation'),
            (r'moreover', 'Standard - fine in moderation'),
        ]
    }
    
    # Topic-specific clichés - used for DETECTION only
    TOPIC_CLICHES = {
        'technology': [
            (r'double-edged sword', 'Overused metaphor - describe specific pros and cons'),
            (r'cutting-edge technology', 'Cliché - be specific about which technology'),
            (r'revolutionized the way we live', 'Overused - give specific examples'),
        ],
        'environment': [
            (r'greenhouse effect', 'Basic term - acceptable but be specific'),
            (r'carbon footprint', 'Common term - acceptable with specific numbers'),
            (r'leave for future generations', 'Cliché - be specific about what actions'),
        ],
        'education': [
            (r'key to success', 'Cliché - explain why it leads to success'),
            (r'foundation of society', 'Overused - give specific examples'),
            (r'shape the future', 'Template phrase - describe how'),
        ]
    }
    
    def detect(self, essay: str, topic: str = None) -> Dict:
        """Analyze essay for template usage - PURE ANALYSIS, NO GENERATION"""
        
        if not essay or len(essay.strip()) < 50:
            return {
                'template_score': 0,
                'total_matches': [],
                'risk_assessment': 'insufficient_text',
                'originality_percentage': 100,
                'suggestions': ['Write a longer essay for template analysis']
            }
        
        essay_lower = essay.lower()
        
        results = {
            'template_score': 0,
            'total_matches': [],
            'risk_assessment': 'low',
            'originality_percentage': 100,
            'suggestions': []
        }
        
        # Check high risk phrases
        high_risk_count = 0
        for pattern, desc in self.TEMPLATE_PHRASES['high_risk']:
            try:
                matches = re.findall(pattern, essay_lower, re.IGNORECASE)
                if matches:
                    high_risk_count += len(matches)
                    results['total_matches'].append({
                        'phrase': pattern.replace(r'\\', '').replace(r'\b', '').replace(r'\s+', ' '),
                        'description': desc,
                        'risk': 'high',
                        'count': len(matches)
                    })
            except re.error:
                # Skip invalid patterns
                continue
        
        # Check medium risk phrases
        medium_risk_count = 0
        for pattern, desc in self.TEMPLATE_PHRASES['medium_risk']:
            try:
                matches = re.findall(pattern, essay_lower, re.IGNORECASE)
                if matches:
                    medium_risk_count += len(matches)
                    results['total_matches'].append({
                        'phrase': pattern.replace(r'\\', '').replace(r'\b', '').replace(r'\s+', ' '),
                        'description': desc,
                        'risk': 'medium',
                        'count': len(matches)
                    })
            except re.error:
                continue
        
        # Calculate template score (0-100, higher = more templates)
        total_cliches = high_risk_count * 3 + medium_risk_count
        template_score = min(100, total_cliches * 12)
        results['template_score'] = template_score
        
        # Assess risk and calculate originality
        if high_risk_count > 0:
            results['risk_assessment'] = 'high'
            results['originality_percentage'] = max(0, 100 - (high_risk_count * 20))
            results['suggestions'].append(" Avoid memorized phrases - write naturally in your own words")
        elif medium_risk_count > 3:
            results['risk_assessment'] = 'medium'
            results['originality_percentage'] = max(0, 100 - (medium_risk_count * 8))
            results['suggestions'].append(" Some phrases sound memorized. Express ideas in your own words")
        elif medium_risk_count > 0:
            results['risk_assessment'] = 'low-medium'
            results['originality_percentage'] = max(50, 90 - (medium_risk_count * 5))
        else:
            results['risk_assessment'] = 'low'
            results['originality_percentage'] = min(100, 85 + (10 - high_risk_count) * 2)
            if results['originality_percentage'] < 70:
                results['originality_percentage'] = 70
        
        # Add topic-specific detection
        if topic:
            topic_key = topic.lower()
            for key in self.TOPIC_CLICHES:
                if key in topic_key or topic_key in key:
                    for pattern, desc in self.TOPIC_CLICHES[key]:
                        try:
                            if re.search(pattern, essay_lower, re.IGNORECASE):
                                results['total_matches'].append({
                                    'phrase': pattern,
                                    'description': desc,
                                    'risk': 'topic',
                                    'count': 1
                                })
                                results['originality_percentage'] = max(0, results['originality_percentage'] - 8)
                        except re.error:
                            continue
                    break
        
        # Cap originality percentage
        results['originality_percentage'] = min(100, max(0, results['originality_percentage']))
        
        # Generate alternative suggestions based on findings
        if results['total_matches']:
            if high_risk_count > 0:
                results['suggestions'].insert(0, " Use original, specific examples instead of generic phrases")
                results['suggestions'].insert(1, " Express your opinion directly: 'I believe...' not 'It is widely believed that...'")
            else:
                results['suggestions'].insert(0, " Try to use more varied sentence structures")
        
        # Add band impact estimate
        if results['risk_assessment'] == 'high':
            results['estimated_band_penalty'] = -1.0
            results['band_impact'] = "High template usage may reduce your band by 0.5-1.0"
        elif results['risk_assessment'] == 'medium':
            results['estimated_band_penalty'] = -0.5
            results['band_impact'] = "Moderate template usage may slightly reduce your band"
        else:
            results['estimated_band_penalty'] = 0
            results['band_impact'] = "Good originality - no template penalty"
        
        return results
    
    def get_template_summary(self, essay: str) -> Dict:
        """Get quick summary of template usage"""
        results = self.detect(essay)
        return {
            'risk_level': results['risk_assessment'],
            'originality_score': results['originality_percentage'],
            'template_count': len(results['total_matches']),
            'band_impact': results.get('band_impact', 'No significant impact'),
            'top_violation': results['total_matches'][0]['description'] if results['total_matches'] else None
        }
    
    def suggest_natural_alternatives(self, phrase: str) -> List[str]:
        """Suggest natural alternatives for a template phrase - GUIDANCE only"""
        alternatives = {
            'in this day and age': ['Today', 'Currently', 'In modern times'],
            'it is a widely held belief that': ['Many people believe that', 'Some argue that'],
            'there is no denying that': ['Clearly', 'Undoubtedly', 'It is clear that'],
            'every coin has two sides': ['While there are benefits, there are also drawbacks'],
            'plays an increasingly important role': ['is becoming more important', 'has growing significance'],
            'with the development of': ['As technology/society advances', 'Due to recent developments'],
        }
        
        return alternatives.get(phrase.lower(), ['Write naturally without memorized phrases'])


# Factory function
def create_anti_template_detector():
    """Factory function to create AntiTemplateDetector"""
    return AntiTemplateDetector()


# Do NOT create instance here - will be created by app
# anti_template = AntiTemplateDetector() # REMOVED