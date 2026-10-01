# modules/ielts/writing/data_to_text.py
"""AI-guided mapping from chart data to descriptive text for Task 1 - PURE ANALYSIS ONLY"""

import re
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

# Trend vocabulary for detection - used for ANALYSIS only
TREND_VOCAB = {
    'increase': ['rose', 'increased', 'grew', 'climbed', 'surged', 'soared', 'peaked'],
    'decrease': ['fell', 'decreased', 'dropped', 'declined', 'plummeted', 'dipped'],
    'stable': ['remained stable', 'stayed constant', 'plateaued', 'leveled off'],
}


class DataToTextMapper:
    """Map chart data to descriptive text for Task 1 evaluation - PURE ANALYSIS ONLY"""
    
    def __init__(self, ai_engine=None):
        """Initialize mapper - AI is optional for advanced analysis"""
        self.ai = ai_engine
        print(f"[DataToTextMapper] Initialized - AI available: {self.ai is not None}")
    
    def analyze_task1(self, essay: str, chart_data: Dict, chart_type: str, topic: str) -> Dict:
        """Analyze how well the essay describes the chart data - PURE ANALYSIS"""
        
        if not essay or len(essay.strip()) < 50:
            return {
                'trends_in_data': [],
                'data_mentions': {
                    'data_points_mentioned': 0,
                    'total_data_points': 0,
                    'coverage_percent': 0,
                    'assessment': 'Essay too short for analysis'
                },
                'has_overview': {'has_overview': False, 'feedback': 'Write more content for analysis'},
                'tips': self._get_tips(chart_type),
                'error': 'Essay too short'
            }
        
        words = essay.lower().split()
        
        trends = self._extract_trends(chart_data, chart_type)
        data_mentions = self._count_data_mentions(essay, chart_data, chart_type)
        overview = self._check_overview(essay)
        
        # Generate AI-powered feedback if available
        ai_feedback = None
        if self.ai:
            ai_feedback = self._get_ai_feedback(essay, chart_data, chart_type, topic, data_mentions)
        
        return {
            'trends_in_data': trends,
            'data_mentions': data_mentions,
            'has_overview': overview,
            'sample_description': None, # Removed - no template generation
            'tips': self._get_tips(chart_type),
            'ai_feedback': ai_feedback,
            'chart_type_analyzed': chart_type
        }
    
    def _get_ai_feedback(self, essay: str, chart_data: Dict, chart_type: str, 
                         topic: str, data_mentions: Dict) -> Dict:
        """Get AI-powered feedback on data description - PURE AI"""
        if not self.ai:
            return None
        
        truncated_essay = essay[:1000] if len(essay) > 1000 else essay
        
        prompt = f"""You are an IELTS Task 1 expert. Analyze how well this essay describes the chart data.

CHART TYPE: {chart_type}
TOPIC: {topic}

DATA MENTIONS ANALYSIS:
- Data points mentioned: {data_mentions.get('data_points_mentioned', 0)}/{data_mentions.get('total_data_points', 0)}
- Coverage: {data_mentions.get('coverage_percent', 0)}%

ESSAY:
{truncated_essay}

Return ONLY valid JSON:
{{
    "data_accuracy": "assessment of whether numbers/labels are correct",
    "missing_key_data": ["specific data points that should be mentioned"],
    "comparison_quality": "how well comparisons are made",
    "specific_suggestion": "one concrete improvement tip"
}}"""
        
        try:
            response = self.ai.generate(prompt, max_tokens=400, temperature=0.3)
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                import json
                return json.loads(json_match.group())
        except Exception as e:
            logger.warning(f"AI feedback failed: {e}")
        
        return None
    
    def _extract_trends(self, data: Dict, chart_type: str) -> List[str]:
        """Extract trends from chart data - ANALYSIS ONLY"""
        trends = []
        
        if chart_type in ('line_graph', 'bar_chart'):
            datasets = data.get('datasets', [data])
            values = []
            if datasets:
                first_ds = datasets[0]
                if isinstance(first_ds, dict):
                    values = first_ds.get('values', data.get('values', []))
                else:
                    values = data.get('values', [])
            
            labels = data.get('labels', [])
            
            if values and len(values) > 1:
                if values[-1] > values[0]:
                    trends.append(f"Overall increase trend from {values[0]} to {values[-1]}")
                elif values[-1] < values[0]:
                    trends.append(f"Overall decrease trend from {values[0]} to {values[-1]}")
                else:
                    trends.append(f"Values remained stable around {values[0]}")
                
                if labels:
                    trends.append(f"Data spans from {labels[0]} to {labels[-1]}")
                
                # Find highest and lowest
                max_val = max(values)
                min_val = min(values)
                if max_val != values[0] and max_val != values[-1]:
                    trends.append(f"Peak value of {max_val} occurred mid-period")
                if min_val != values[0] and min_val != values[-1]:
                    trends.append(f"Lowest value of {min_val} occurred mid-period")
        
        elif chart_type == 'pie_chart':
            values = data.get('values', [])
            labels = data.get('labels', [])
            if values and labels:
                max_i = values.index(max(values))
                min_i = values.index(min(values))
                trends.append(f"Largest segment: {labels[max_i]} ({values[max_i]}%)")
                trends.append(f"Smallest segment: {labels[min_i]} ({values[min_i]}%)")
                
                # Check for majority
                if max(values) > 50:
                    trends.append(f"{labels[max_i]} constitutes over half of the total")
        
        elif chart_type == 'table':
            rows = data.get('rows', [])
            headers = data.get('headers', [])
            if rows:
                trends.append(f"Table contains {len(rows)} rows of data")
                if headers:
                    trends.append(f"Categories: {', '.join(str(h) for h in headers[:3])}")
        
        elif chart_type == 'map':
            before = data.get('before', [])
            after = data.get('after', [])
            changes = data.get('changes', [])
            if before and after:
                trends.append(f"Map shows changes from {len(before)} features to {len(after)} features")
                if changes:
                    trends.append(f"Number of changes detected: {len(changes)}")
        
        elif chart_type in ('diagram', 'flow_chart'):
            steps = data.get('steps', []) or data.get('stages', [])
            if steps:
                trends.append(f"Process contains {len(steps)} steps/stages")
                trends.append(f"Starts with: {steps[0] if steps else 'N/A'}")
                if len(steps) > 1:
                    trends.append(f"Ends with: {steps[-1] if steps else 'N/A'}")
        
        return trends
    
    def _count_data_mentions(self, essay: str, data: Dict, chart_type: str) -> Dict:
        """Count how many data points are mentioned in essay - ANALYSIS ONLY"""
        essay_lower = essay.lower()
        
        all_values = []
        all_labels = []
        
        if chart_type in ('line_graph', 'bar_chart'):
            datasets = data.get('datasets', [])
            for ds in datasets:
                if isinstance(ds, dict):
                    all_values.extend(ds.get('values', []))
                    if ds.get('label'):
                        all_labels.append(ds.get('label'))
            all_labels.extend(data.get('labels', []))
        
        elif chart_type == 'pie_chart':
            all_values = data.get('values', [])
            all_labels = data.get('labels', [])
        
        elif chart_type == 'table':
            for row in data.get('rows', []):
                for cell in row:
                    if isinstance(cell, (int, float)):
                        all_values.append(cell)
                    elif isinstance(cell, str) and len(cell) > 2:
                        all_labels.append(cell)
            all_labels.extend(data.get('headers', []))
        
        elif chart_type == 'map':
            all_labels.extend(data.get('before', []))
            all_labels.extend(data.get('after', []))
        
        elif chart_type in ('diagram', 'flow_chart'):
            all_labels.extend(data.get('steps', []))
            all_labels.extend(data.get('stages', []))
            all_labels.extend(data.get('descriptions', []))
        
        # Count values mentioned
        values_mentioned = 0
        for v in all_values:
            if str(v) in essay_lower or f"{v}%" in essay_lower:
                values_mentioned += 1
        
        # Count labels mentioned
        labels_mentioned = 0
        for l in all_labels:
            if str(l).lower() in essay_lower:
                labels_mentioned += 1
        
        total_data_points = len(all_values) + len(all_labels)
        total_mentioned = values_mentioned + labels_mentioned
        
        coverage = round(total_mentioned / max(total_data_points, 1) * 100)
        
        # Determine assessment
        if coverage >= 70:
            assessment = "Excellent - mentions most key data points"
        elif coverage >= 50:
            assessment = "Good - covers main data points"
        elif coverage >= 30:
            assessment = "Adequate - mention more specific numbers"
        else:
            assessment = "Needs improvement - include specific data from chart"
        
        return {
            'data_points_mentioned': total_mentioned,
            'total_data_points': total_data_points,
            'values_mentioned': values_mentioned,
            'labels_mentioned': labels_mentioned,
            'coverage_percent': coverage,
            'assessment': assessment,
        }
    
    def _check_overview(self, essay: str) -> Dict:
        """Check for overview statement - ANALYSIS ONLY"""
        essay_lower = essay.lower()
        
        overview_indicators = [
            r'overall', r'in summary', r'in general', r'it is clear that', 
            r'it is evident that', r'as can be seen', r'to summarize'
        ]
        
        has_overview = any(re.search(pattern, essay_lower) for pattern in overview_indicators)
        
        # Check if overview is at beginning (first 2 sentences)
        sentences = [s.strip() for s in re.split(r'[.!?]+', essay) if s.strip()]
        overview_position = "unknown"
        
        if has_overview and sentences:
            first_sentence = sentences[0].lower()
            if any(word in first_sentence for word in ['overall', 'in summary', 'in general']):
                overview_position = "good_position"
            else:
                overview_position = "late_or_missing"
        
        return {
            'has_overview': has_overview,
            'position': overview_position,
            'feedback': 'Good overview present' if has_overview else 'Add an overview sentence starting with "Overall..."'
        }
    
    def _get_tips(self, chart_type: str) -> List[str]:
        """Get tips for describing this chart type - GUIDANCE ONLY"""
        tips = [
            " Start with an overview sentence: 'Overall, the chart shows...'",
            " Include specific numbers from the chart",
            " Make comparisons between different categories"
        ]
        
        if chart_type == 'line_graph':
            tips.extend([
                " Describe trends: increased steadily, decreased sharply, fluctuated, peaked at",
                " Use time-specific language: 'between 2010 and 2020', 'over the period'"
            ])
        elif chart_type == 'bar_chart':
            tips.extend([
                " Compare categories: 'significantly higher than', 'comparable to'",
                " Highlight the highest and lowest bars"
            ])
        elif chart_type == 'pie_chart':
            tips.extend([
                " Use fractions: 'a third', 'a quarter', 'half', 'the majority'",
                " Mention the largest and smallest segments"
            ])
        elif chart_type == 'table':
            tips.extend([
                " Compare across rows and columns",
                " Highlight the highest and lowest values"
            ])
        elif chart_type == 'map':
            tips.extend([
                " Describe what changed and what remained the same",
                " Use location language: 'to the north', 'replaced by'"
            ])
        elif chart_type in ('diagram', 'flow_chart'):
            tips.extend([
                " Describe the sequence: 'First... then... after that... finally'",
                " Use passive voice: 'the material is then processed'"
            ])
        
        return tips
    
    def get_data_summary(self, chart_data: Dict, chart_type: str) -> Dict:
        """Get a summary of the chart data - ANALYSIS ONLY"""
        summary = {
            'chart_type': chart_type,
            'has_data': False,
            'key_insights': []
        }
        
        if chart_type in ('line_graph', 'bar_chart'):
            datasets = chart_data.get('datasets', [])
            labels = chart_data.get('labels', [])
            
            if datasets and labels:
                summary['has_data'] = True
                summary['x_axis'] = f"Categories: {', '.join(str(l) for l in labels[:5])}"
                summary['series_count'] = len(datasets)
                
                for ds in datasets:
                    if isinstance(ds, dict) and 'values' in ds:
                        values = ds['values']
                        if values:
                            summary['key_insights'].append({
                                'series': ds.get('label', 'Data'),
                                'range': f"{min(values)} to {max(values)}",
                                'trend': 'increasing' if values[-1] > values[0] else 'decreasing' if values[-1] < values[0] else 'stable'
                            })
        
        elif chart_type == 'pie_chart':
            labels = chart_data.get('labels', [])
            values = chart_data.get('values', [])
            
            if labels and values:
                summary['has_data'] = True
                max_idx = values.index(max(values))
                summary['largest_segment'] = f"{labels[max_idx]} ({values[max_idx]}%)"
                summary['segments_count'] = len(labels)
        
        elif chart_type == 'table':
            rows = chart_data.get('rows', [])
            headers = chart_data.get('headers', [])
            
            if rows:
                summary['has_data'] = True
                summary['rows_count'] = len(rows)
                summary['columns_count'] = len(headers) if headers else len(rows[0]) if rows else 0
        
        return summary


# Factory function
def create_data_mapper(ai_engine=None):
    """Factory function to create DataToTextMapper"""
    return DataToTextMapper(ai_engine)


# Do NOT create instance here - will be created by app
# data_mapper = DataToTextMapper() # REMOVED