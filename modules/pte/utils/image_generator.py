# modules/pte/utils/image_generator.py
"""PTE Image Generator - Generate charts/graphs for Describe Image tasks"""

import os
import uuid
import base64
import logging
import random
from io import BytesIO
from typing import Dict, Optional, List, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Try to import matplotlib
try:
    import matplotlib
    matplotlib.use('Agg') # Use non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.patches import Rectangle
    import numpy as np
    MATPLOTLIB_AVAILABLE = True
    logger.info(" Matplotlib loaded successfully")
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    logger.warning(" Matplotlib not installed. Image generation disabled.")

# Static directory for saving images
STATIC_IMAGE_DIR = os.path.join('static', 'images', 'pte_images')
os.makedirs(STATIC_IMAGE_DIR, exist_ok=True)


class PTEImageGenerator:
    """Generate PTE Describe Image charts and graphs."""
    
    # Color palettes for charts
    COLORS = [
        '#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7',
        '#DDA0DD', '#98D8C8', '#F7DC6F', '#BB8FCE', '#85C1E9',
        '#F1948A', '#82E0AA', '#F8C471', '#85929E', '#73C6B6'
    ]
    
    # Chart types with their descriptions
    CHART_TYPES = {
        'bar_chart': {
            'name': 'Bar Chart',
            'description': 'A bar chart showing data distribution across categories.'
        },
        'line_graph': {
            'name': 'Line Graph',
            'description': 'A line graph showing trends over time.'
        },
        'pie_chart': {
            'name': 'Pie Chart',
            'description': 'A pie chart showing percentage distribution.'
        },
        'column_graph': {
            'name': 'Column Graph',
            'description': 'A column graph comparing values across categories.'
        },
        'stacked_bar': {
            'name': 'Stacked Bar Chart',
            'description': 'A stacked bar chart showing component breakdown.'
        },
        'scatter_plot': {
            'name': 'Scatter Plot',
            'description': 'A scatter plot showing relationships between variables.'
        },
        'area_chart': {
            'name': 'Area Chart',
            'description': 'An area chart showing cumulative values over time.'
        },
        'flowchart': {
            'name': 'Flowchart',
            'description': 'A flowchart showing a process or sequence.'
        },
        'gantt_chart': {
            'name': 'Gantt Chart',
            'description': 'A Gantt chart showing project timeline.'
        },
        'organizational_chart': {
            'name': 'Organizational Chart',
            'description': 'An organizational chart showing hierarchy.'
        },
        'venn_diagram': {
            'name': 'Venn Diagram',
            'description': 'A Venn diagram showing overlapping relationships.'
        },
        'radar_chart': {
            'name': 'Radar Chart',
            'description': 'A radar chart showing multi-dimensional data.'
        },
        'map': {
            'name': 'Map',
            'description': 'A map showing geographical distribution.'
        },
        'process_diagram': {
            'name': 'Process Diagram',
            'description': 'A process diagram showing steps in a sequence.'
        },
        'table': {
            'name': 'Table',
            'description': 'A table showing structured data.'
        }
    }
    
    def __init__(self):
        """Initialize PTE Image Generator."""
        self.image_cache_dir = STATIC_IMAGE_DIR
        os.makedirs(self.image_cache_dir, exist_ok=True)
        logger.info("PTEImageGenerator initialized")
    
    def generate_image(self, chart_type: str, difficulty: str = 'medium', 
                       save_to_disk: bool = True) -> Dict[str, Any]:
        """
        Generate an image for Describe Image task.
        
        Args:
            chart_type: Type of chart (bar_chart, line_graph, etc.)
            difficulty: Difficulty level (easy, medium, hard)
            save_to_disk: Whether to save the image to disk
        
        Returns:
            Dict with 'image_url', 'base64', 'description', 'success'
        """
        if not MATPLOTLIB_AVAILABLE:
            return self._get_placeholder(chart_type, difficulty)
        
        try:
            # Get chart generator function
            generator = getattr(self, f'_generate_{chart_type}', None)
            if not generator:
                logger.warning(f"Chart type '{chart_type}' not supported, using placeholder")
                return self._get_placeholder(chart_type, difficulty)
            
            # Generate the chart
            fig, description = generator(difficulty)
            
            # Convert to base64
            buffer = BytesIO()
            fig.savefig(buffer, format='png', dpi=100, bbox_inches='tight', facecolor='white')
            buffer.seek(0)
            base64_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
            plt.close(fig)
            
            # Save to disk if requested
            image_url = None
            if save_to_disk:
                filename = f"{chart_type}_{uuid.uuid4().hex[:8]}.png"
                filepath = os.path.join(self.image_cache_dir, filename)
                with open(filepath, 'wb') as f:
                    f.write(buffer.getvalue())
                image_url = f"/static/images/pte_images/{filename}"
            
            return {
                'success': True,
                'image_url': image_url,
                'base64': f"data:image/png;base64,{base64_str}",
                'description': description or self.CHART_TYPES.get(chart_type, {}).get('description', ''),
                'chart_type': chart_type,
                'difficulty': difficulty
            }
            
        except Exception as e:
            logger.error(f"Image generation failed: {e}")
            return self._get_placeholder(chart_type, difficulty)
    
    # NEW: Regenerate only the image file (keeps description unchanged)
    def regenerate_image(self, chart_type: str, description: str, difficulty: str = 'medium') -> Optional[Dict[str, Any]]:
        """
        Regenerate an image of the given chart type and return new URL/base64.
        Does NOT modify the description – uses the provided one.
        """
        if not MATPLOTLIB_AVAILABLE:
            logger.warning("Matplotlib not available; cannot regenerate image.")
            return None

        try:
            generator = getattr(self, f'_generate_{chart_type}', None)
            if not generator:
                logger.warning(f"Chart type '{chart_type}' not supported for regeneration")
                return None

            # Generate the chart; we ignore the description returned and keep ours
            fig, _ = generator(difficulty)

            buffer = BytesIO()
            fig.savefig(buffer, format='png', dpi=100, bbox_inches='tight', facecolor='white')
            buffer.seek(0)
            base64_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
            plt.close(fig)

            # Save to disk
            filename = f"{chart_type}_{uuid.uuid4().hex[:8]}.png"
            filepath = os.path.join(self.image_cache_dir, filename)
            with open(filepath, 'wb') as f:
                f.write(buffer.getvalue())
            image_url = f"/static/images/pte_images/{filename}"

            return {
                'success': True,
                'image_url': image_url,
                'base64': f"data:image/png;base64,{base64_str}",
                'description': description, # Keep the existing description
                'chart_type': chart_type,
                'difficulty': difficulty
            }
        except Exception as e:
            logger.error(f"Image regeneration failed: {e}")
            return None

    def _get_placeholder(self, chart_type: str, difficulty: str) -> Dict[str, Any]:
        """Return placeholder data when image generation fails."""
        return {
            'success': False,
            'image_url': None,
            'base64': '',
            'description': f"A {self.CHART_TYPES.get(chart_type, {}).get('name', chart_type)} placeholder.",
            'chart_type': chart_type,
            'difficulty': difficulty,
            'error': 'Matplotlib not available'
        }
    
    # ============ CHART GENERATORS ============
    
    def _generate_bar_chart(self, difficulty: str = 'medium'):
        """Generate a bar chart."""
        fig, ax = plt.subplots(figsize=(8, 5))
        
        categories = ['Category A', 'Category B', 'Category C', 'Category D', 'Category E']
        if difficulty == 'easy':
            values = [random.randint(30, 70) for _ in range(5)]
        elif difficulty == 'hard':
            values = [random.randint(10, 90) for _ in range(5)]
        else:
            values = [random.randint(20, 80) for _ in range(5)]
        
        colors = random.sample(self.COLORS, len(categories))
        bars = ax.bar(categories, values, color=colors, edgecolor='black', linewidth=1)
        
        # Add value labels
        for bar, val in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1, 
                    str(val), ha='center', va='bottom', fontsize=10, fontweight='bold')
        
        ax.set_ylabel('Values', fontsize=12)
        ax.set_title(f'Distribution Across Categories ({difficulty.title()} Level)', fontsize=14)
        ax.grid(axis='y', linestyle='--', alpha=0.7)
        
        description = f"This bar chart shows the distribution of values across five categories. "
        description += f"The highest value is {max(values)} and the lowest is {min(values)}."
        
        return fig, description
    
    def _generate_line_graph(self, difficulty: str = 'medium'):
        """Generate a line graph."""
        fig, ax = plt.subplots(figsize=(8, 5))
        
        years = ['2020', '2021', '2022', '2023', '2024', '2025']
        if difficulty == 'easy':
            values = [20, 35, 45, 55, 70, 85]
        elif difficulty == 'hard':
            base = random.randint(10, 30)
            values = [base + random.randint(0, 10) for _ in range(6)]
            values = [values[0]] + [values[i] + random.randint(5, 15) for i in range(1, 6)]
        else:
            values = [25, 40, 50, 65, 75, 90]
        
        ax.plot(years, values, marker='o', linewidth=2.5, markersize=8, 
                color=random.choice(self.COLORS), label='Trend')
        ax.fill_between(years, values, alpha=0.2, color=random.choice(self.COLORS))
        
        ax.set_xlabel('Year', fontsize=12)
        ax.set_ylabel('Values', fontsize=12)
        ax.set_title(f'Trend Over Time ({difficulty.title()} Level)', fontsize=14)
        ax.grid(True, linestyle='--', alpha=0.7)
        ax.legend()
        
        description = f"This line graph shows the trend from {years[0]} to {years[-1]}. "
        description += f"The values increased from {values[0]} to {values[-1]}."
        
        return fig, description
    
    def _generate_pie_chart(self, difficulty: str = 'medium'):
        """Generate a pie chart."""
        fig, ax = plt.subplots(figsize=(7, 7))
        
        labels = ['Category A', 'Category B', 'Category C', 'Category D']
        if difficulty == 'easy':
            sizes = [40, 30, 20, 10]
        elif difficulty == 'hard':
            sizes = [random.randint(5, 35) for _ in range(4)]
            total = sum(sizes)
            sizes = [round(s/total*100, 1) for s in sizes]
        else:
            sizes = [35, 25, 25, 15]
        
        colors = random.sample(self.COLORS, len(labels))
        explode = (0.05, 0.02, 0.02, 0.02)
        
        wedges, texts, autotexts = ax.pie(
            sizes, labels=labels, colors=colors, explode=explode,
            autopct='%1.1f%%', startangle=90,
            textprops={'fontsize': 11}
        )
        
        ax.set_title(f'Distribution ({difficulty.title()} Level)', fontsize=14)
        
        description = f"This pie chart shows the distribution across four categories. "
        description += f"The largest segment is {labels[sizes.index(max(sizes))]} with {max(sizes)}%."
        
        return fig, description
    
    def _generate_column_graph(self, difficulty: str = 'medium'):
        """Generate a column graph (similar to bar chart)."""
        return self._generate_bar_chart(difficulty)
    
    def _generate_stacked_bar(self, difficulty: str = 'medium'):
        """Generate a stacked bar chart."""
        fig, ax = plt.subplots(figsize=(8, 5))
        
        categories = ['Group 1', 'Group 2', 'Group 3', 'Group 4']
        components = ['Component A', 'Component B', 'Component C']
        
        if difficulty == 'easy':
            data = [[30, 40, 30], [35, 35, 30], [40, 30, 30], [25, 45, 30]]
        else:
            data = [[random.randint(10, 40) for _ in range(3)] for _ in range(4)]
            # Normalize
            for row in data:
                total = sum(row)
                if total > 0:
                    for i in range(len(row)):
                        row[i] = round(row[i]/total*100)
        
        colors = random.sample(self.COLORS, len(components))
        bottom = [0] * len(categories)
        
        for i, comp in enumerate(components):
            values = [row[i] for row in data]
            ax.bar(categories, values, bottom=bottom, label=comp, color=colors[i % len(colors)])
            bottom = [bottom[j] + values[j] for j in range(len(categories))]
        
        ax.set_ylabel('Percentage (%)', fontsize=12)
        ax.set_title(f'Stacked Bar Chart ({difficulty.title()} Level)', fontsize=14)
        ax.legend()
        ax.grid(axis='y', linestyle='--', alpha=0.7)
        
        description = f"This stacked bar chart shows the composition of four groups."
        
        return fig, description
    
    def _generate_table(self, difficulty: str = 'medium'):
        """Generate a table."""
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.axis('off')
        
        headers = ['Category', '2020', '2021', '2022', '2023']
        
        if difficulty == 'easy':
            data = [
                ['Product A', 150, 180, 210, 240],
                ['Product B', 200, 220, 250, 280],
                ['Product C', 100, 120, 140, 160],
                ['Product D', 300, 310, 330, 350]
            ]
        else:
            data = []
            for i in range(4):
                row = [f'Item {chr(65+i)}'] + [random.randint(50, 400) for _ in range(4)]
                data.append(row)
        
        table = ax.table(cellText=data, colLabels=headers, loc='center', 
                         cellLoc='center', colColours=['#f0f0f0']*5)
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1, 1.5)
        
        ax.set_title(f'Data Table ({difficulty.title()} Level)', fontsize=14)
        
        description = f"This table shows data for {len(data)} categories over 4 years."
        
        return fig, description
    
    def _generate_radar_chart(self, difficulty: str = 'medium'):
        """Generate a radar chart."""
        fig, ax = plt.subplots(figsize=(7, 7), subplot_kw=dict(projection='polar'))
        
        categories = ['Speed', 'Power', 'Accuracy', 'Durability', 'Efficiency']
        num_vars = len(categories)
        
        if difficulty == 'easy':
            values = [4, 3, 5, 4, 5]
        else:
            values = [random.randint(2, 5) for _ in range(num_vars)]
        
        angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()
        values_plot = values + values[:1]
        angles_plot = angles + angles[:1]
        
        ax.plot(angles_plot, values_plot, 'o-', linewidth=2, color=random.choice(self.COLORS))
        ax.fill(angles_plot, values_plot, alpha=0.25, color=random.choice(self.COLORS))
        
        ax.set_xticks(angles)
        ax.set_xticklabels(categories, fontsize=10)
        ax.set_ylim(0, 5)
        ax.set_yticks([1, 2, 3, 4, 5])
        ax.set_yticklabels(['1', '2', '3', '4', '5'], fontsize=8)
        ax.grid(True)
        
        ax.set_title(f'Radar Chart ({difficulty.title()} Level)', fontsize=14, pad=20)
        
        description = f"This radar chart shows performance across {num_vars} categories."
        
        return fig, description
    
    def _generate_scatter_plot(self, difficulty: str = 'medium'):
        """Generate a scatter plot."""
        fig, ax = plt.subplots(figsize=(8, 5))
        
        if difficulty == 'easy':
            n = 20
            x = np.random.rand(n) * 100
            y = 2 * x + np.random.randn(n) * 10
        else:
            n = 40
            x = np.random.rand(n) * 100
            y = 1.5 * x + np.random.randn(n) * 15
        
        ax.scatter(x, y, alpha=0.7, s=50, color=random.choice(self.COLORS))
        
        ax.set_xlabel('Variable X', fontsize=12)
        ax.set_ylabel('Variable Y', fontsize=12)
        ax.set_title(f'Scatter Plot ({difficulty.title()} Level)', fontsize=14)
        ax.grid(True, linestyle='--', alpha=0.5)
        
        description = f"This scatter plot shows the relationship between two variables with {n} data points."
        
        return fig, description
    
    # Placeholder for other chart types
    def _generate_flowchart(self, difficulty: str = 'medium'):
        """Generate a simple flowchart."""
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.axis('off')
        
        steps = ['Start', 'Step 1', 'Decision?', 'Step 2', 'End']
        colors = ['#FF6B6B', '#4ECDC4', '#FFEAA7', '#45B7D1', '#96CEB4']
        
        y_positions = [4, 3, 2, 1, 0]
        for i, (step, color, y) in enumerate(zip(steps, colors, y_positions)):
            rect = Rectangle((3, y), 2, 0.6, facecolor=color, edgecolor='black', linewidth=1)
            ax.add_patch(rect)
            ax.text(4, y + 0.3, step, ha='center', va='center', fontsize=10, fontweight='bold')
            
            # Draw arrows
            if i < len(steps) - 1:
                ax.annotate('', xy=(4, y), xytext=(4, y + 0.6),
                           arrowprops=dict(arrowstyle='->', color='gray', lw=1.5))
        
        ax.set_xlim(2, 6)
        ax.set_ylim(-0.5, 5)
        ax.set_title(f'Flowchart ({difficulty.title()} Level)', fontsize=14)
        
        description = "This flowchart shows a simple process with 5 steps."
        
        return fig, description
    
    def _generate_map(self, difficulty: str = 'medium'):
        """Generate a simple map."""
        fig, ax = plt.subplots(figsize=(7, 5))
        
        # Simple map with regions
        regions = ['North', 'East', 'South', 'West']
        colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4']
        
        # Create a simple grid map
        for i, (region, color) in enumerate(zip(regions, colors)):
            x = i % 2
            y = i // 2
            rect = Rectangle((x*2, y*2), 1.8, 1.8, facecolor=color, edgecolor='black', linewidth=2)
            ax.add_patch(rect)
            ax.text(x*2 + 0.9, y*2 + 0.9, region, ha='center', va='center', fontsize=10, fontweight='bold')
        
        ax.set_xlim(-0.5, 4)
        ax.set_ylim(-0.5, 4)
        ax.axis('off')
        ax.set_title(f'Map ({difficulty.title()} Level)', fontsize=14)
        
        description = "This map shows four regions: North, East, South, and West."
        
        return fig, description
    
    def _generate_process_diagram(self, difficulty: str = 'medium'):
        """Generate a process diagram."""
        return self._generate_flowchart(difficulty)
    
    def _generate_venn_diagram(self, difficulty: str = 'medium'):
        """Generate a Venn diagram."""
        fig, ax = plt.subplots(figsize=(6, 6))
        
        # Simple Venn diagram with 3 circles
        from matplotlib.patches import Circle
        
        colors = ['#FF6B6B', '#4ECDC4', '#45B7D1']
        circles = [
            Circle((-0.5, 0.3), 1.2, facecolor=colors[0], alpha=0.5, edgecolor='black', linewidth=1.5),
            Circle((0.5, 0.3), 1.2, facecolor=colors[1], alpha=0.5, edgecolor='black', linewidth=1.5),
            Circle((0, -0.6), 1.2, facecolor=colors[2], alpha=0.5, edgecolor='black', linewidth=1.5)
        ]
        
        labels = ['A', 'B', 'C']
        positions = [(-0.5, 0.3), (0.5, 0.3), (0, -0.6)]
        
        for circle, label, pos in zip(circles, labels, positions):
            ax.add_patch(circle)
            ax.text(pos[0], pos[1], label, ha='center', va='center', fontsize=14, fontweight='bold')
        
        # Intersection labels
        ax.text(0, 0.3, 'A∩B', ha='center', va='center', fontsize=10)
        ax.text(-0.25, -0.15, 'A∩C', ha='center', va='center', fontsize=10)
        ax.text(0.25, -0.15, 'B∩C', ha='center', va='center', fontsize=10)
        ax.text(0, -0.3, 'A∩B∩C', ha='center', va='center', fontsize=10)
        
        ax.set_xlim(-2, 2)
        ax.set_ylim(-2, 2)
        ax.axis('off')
        ax.set_title(f'Venn Diagram ({difficulty.title()} Level)', fontsize=14)
        
        description = "This Venn diagram shows the relationships between three sets: A, B, and C."
        
        return fig, description
    
    def _generate_gantt_chart(self, difficulty: str = 'medium'):
        """Generate a Gantt chart."""
        fig, ax = plt.subplots(figsize=(8, 4))
        
        tasks = ['Task 1', 'Task 2', 'Task 3', 'Task 4']
        start_days = [0, 3, 1, 6]
        durations = [2, 4, 3, 2]
        colors = random.sample(self.COLORS, len(tasks))
        
        for i, (task, start, duration, color) in enumerate(zip(tasks, start_days, durations, colors)):
            ax.barh(i, duration, left=start, color=color, edgecolor='black', linewidth=1)
            ax.text(start + duration/2, i, f'{duration}d', ha='center', va='center', fontsize=9, fontweight='bold')
        
        ax.set_yticks(range(len(tasks)))
        ax.set_yticklabels(tasks)
        ax.set_xlabel('Days', fontsize=12)
        ax.set_title(f'Gantt Chart ({difficulty.title()} Level)', fontsize=14)
        ax.grid(axis='x', linestyle='--', alpha=0.5)
        ax.set_xlim(0, 10)
        
        description = "This Gantt chart shows a project timeline with 4 tasks."
        
        return fig, description
    
    def _generate_organizational_chart(self, difficulty: str = 'medium'):
        """Generate an organizational chart."""
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.axis('off')
        
        # Simple org chart
        positions = [
            (4, 4, 'CEO', '#FF6B6B'),
            (2, 2.5, 'VP Sales', '#4ECDC4'),
            (6, 2.5, 'VP Tech', '#45B7D1'),
            (1, 1, 'Manager A', '#96CEB4'),
            (3, 1, 'Manager B', '#FFEAA7'),
            (5, 1, 'Manager C', '#DDA0DD'),
            (7, 1, 'Manager D', '#98D8C8')
        ]
        
        # Draw connections first
        connections = [(4,4, 2,2.5), (4,4, 6,2.5), (2,2.5, 1,1), (2,2.5, 3,1),
                       (6,2.5, 5,1), (6,2.5, 7,1)]
        
        for x1, y1, x2, y2 in connections:
            ax.plot([x1, x2], [y1, y2], color='gray', linewidth=1.5, linestyle='-')
        
        # Draw boxes
        for x, y, label, color in positions:
            rect = Rectangle((x-1.5, y-0.4), 3, 0.8, facecolor=color, edgecolor='black', linewidth=1.5)
            ax.add_patch(rect)
            ax.text(x, y, label, ha='center', va='center', fontsize=10, fontweight='bold')
        
        ax.set_xlim(-1, 9)
        ax.set_ylim(0, 5.5)
        ax.set_title(f'Organizational Chart ({difficulty.title()} Level)', fontsize=14)
        
        description = "This organizational chart shows a simple company structure."
        
        return fig, description
    
    def _generate_area_chart(self, difficulty: str = 'medium'):
        """Generate an area chart."""
        fig, ax = plt.subplots(figsize=(8, 5))
        
        years = ['2020', '2021', '2022', '2023', '2024', '2025']
        if difficulty == 'easy':
            values = [10, 25, 40, 55, 70, 85]
        else:
            values = [random.randint(10, 30) for _ in range(6)]
            values = [values[0]] + [values[i] + random.randint(5, 15) for i in range(1, 6)]
        
        ax.fill_between(years, values, color=random.choice(self.COLORS), alpha=0.6)
        ax.plot(years, values, color='black', linewidth=2)
        
        ax.set_xlabel('Year', fontsize=12)
        ax.set_ylabel('Values', fontsize=12)
        ax.set_title(f'Area Chart ({difficulty.title()} Level)', fontsize=14)
        ax.grid(True, linestyle='--', alpha=0.5)
        
        description = f"This area chart shows cumulative values from {years[0]} to {years[-1]}."
        
        return fig, description


# ============ SINGLETON INSTANCE ============
pte_image_generator = PTEImageGenerator()


# ============ EXPORTS ============
__all__ = [
    'PTEImageGenerator',
    'pte_image_generator',
    'MATPLOTLIB_AVAILABLE'
]