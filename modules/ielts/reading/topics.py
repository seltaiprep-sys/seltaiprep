"""IELTS Reading Topics – 300+ authentic exam topics with difficulty levels."""

# ─── DEFAULT TOPICS (General / Mixed) ────────────────────────────────
DEFAULT_TOPICS = [
    # Science & Technology
    "biodiversity", "climate_modeling", "neural_pathways", "protein_folding",
    "bioinformatics", "artificial_intelligence", "quantum_computing",
    "space_exploration", "renewable_energy", "genetic_research",
    "marine_biology", "nanotechnology", "robotics", "cybersecurity",
    "biotechnology", "cryogenics", "dark_matter", "exoplanets",
    "fusion_energy", "gene_editing", "green_technology", "hydrogen_fuel",
    "internet_of_things", "machine_learning", "materials_science",
    "neural_networks", "particle_physics", "photovoltaics", "robotics_ethics",
    "semiconductors", "space_tourism", "sustainable_transport", "telemedicine",
    "virtual_reality", "wearable_technology", "autonomous_vehicles",
    
    # Environment & Ecology
    "urban_life", "social_evolution", "fractal_geometry", "criminal_justice",
    "remote_work", "aging_population", "plant_science", "economic_development",
    "food_security", "digital_education", "mental_health_awareness",
    "sustainable_agriculture", "ocean_conservation", "pandemic_response",
    "air_quality", "biodiversity_loss", "carbon_capture", "circular_economy",
    "conservation_biology", "coral_reefs", "deforestation", "desertification",
    "ecological_footprint", "ecosystem_services", "endangered_species",
    "environmental_policy", "green_buildings", "groundwater_depletion",
    "habitat_fragmentation", "marine_ecosystems", "microplastics", "noise_pollution",
    "ozone_depletion", "plastic_recycling", "pollinator_decline", "rainforests",
    "soil_erosion", "urban_forestry", "waste_management", "water_scarcity",
    "wetland_restoration", "wildfire_management", "wind_energy",
    
    # History & Archaeology
    "historical_archaeology", "ancient_civilizations", "art_history",
    "cultural_heritage", "museum_studies", "architectural_history",
    "anthropology", "biblical_archaeology", "classical_antiquity", "colonial_history",
    "cultural_evolution", "dynastic_china", "egyptology", "historical_cartography",
    "historical_linguistics", "human_migration", "industrial_revolution",
    "medieval_history", "military_history", "native_american_studies",
    "ocean_archaeology", "paleontology", "renaissance_studies", "silk_road",
    "slave_trade_history", "social_history", "space_archaeology",
    "underwater_archaeology", "urban_history", "viking_studies",
    "world_war_history", "ancient_philosophy", "historical_geography",
    
    # Society & Culture
    "cultural_anthropology", "demographic_change", "gender_studies",
    "globalization", "immigration", "indigenous_cultures", "linguistic_diversity",
    "media_studies", "multiculturalism", "population_growth", "social_equality",
    "social_movements", "urban_sociology", "youth_culture", "digital_culture",
    "folk_traditions", "cultural_identity", "language_revitalization",
    "religious_studies", "sustainable_tourism", "festival_cultures",
    "modern_architecture", "contemporary_art", "film_studies", "music_history",
    
    # Health & Medicine
    "epidemiology", "nutrition_science", "public_health", "biomedical_engineering",
    "clinical_research", "pharmaceutical_development", "genomic_medicine",
    "infectious_diseases", "mental_health", "neurology", "cardiovascular_health",
    "diabetes_research", "digital_health", "epidemic_response", "exercise_science",
    "food_safety", "global_health", "immunology", "longevity_research",
    "medical_ethics", "neuroplasticity", "obesity_epidemic", "occupational_health",
    "pain_management", "palliative_care", "personalized_medicine", "psychology",
    "public_health_policy", "reproductive_health", "sleep_science", "smoking_cessation",
    "sports_medicine", "stem_cell_research", "telehealth", "vaccination",
    "wellness_industry", "women_health", "youth_health",
    
    # Business & Economics
    "global_trade", "financial_markets", "entrepreneurship", "supply_chain",
    "business_ethics", "economic_policy", "consumer_behavior", "corporate_governance",
    "e_commerce", "economic_development", "financial_literacy", "foreign_investment",
    "gig_economy", "industrial_policy", "international_business", "marketing_strategies",
    "monetary_policy", "post_covid_economy", "real_estate", "retail_innovation",
    "sustainable_business", "tax_policy", "trade_agreements", "venture_capital",
    "work_automation", "workplace_diversity", "digital_currency", "sharing_economy",
    "green_finance", "corporate_social_responsibility",
    
    # Education & Psychology
    "educational_psychology", "learning_theories", "child_development",
    "cognitive_science", "behavioral_economics", "educational_technology",
    "student_motivation", "curriculum_design", "early_childhood_education",
    "higher_education", "language_acquisition", "lifelong_learning",
    "neuroeducation", "online_learning", "pedagogy", "special_education",
    "teacher_training", "thinking_skills", "vocational_education",
    
    # Politics & Law
    "human_rights", "constitutional_law", "international_relations",
    "criminal_justice_system", "public_policy", "political_ideologies",
    "diplomacy", "election_systems", "foreign_policy", "governance",
    "judicial_system", "legal_ethics", "peacekeeping", "political_economy",
    "public_administration", "rule_of_law", "security_studies",
    "social_welfare_policy", "political_campaigns", "international_law",
    
    # Sports & Recreation
    "sports_psychology", "athletic_performance", "fan_behavior",
    "sports_injuries", "doping_in_sports", "women_in_sports",
    "paralympic_movement", "sports_economics", "recreation_and_leisure",
    "adventure_sports", "team_dynamics", "sports_media", "youth_sports",
    
    # Arts & Entertainment
    "art_therapy", "creative_writing", "digital_arts", "entertainment_industry",
    "film_production", "music_therapy", "performance_arts", "visual_arts",
    
    # Agriculture & Food
    "agricultural_technology", "food_chemistry", "organic_farming",
    "food_security_policy", "aquaculture", "crop_improvement", "dairy_science",
    "food_processing", "horticulture", "livestock_management", "precision_agriculture",
    "soil_health", "sustainable_fisheries", "vertical_farming", "food_waste_reduction",
    
    # Transport & Infrastructure
    "urban_transport", "public_transport", "aviation_technology",
    "railway_development", "smart_cities", "infrastructure_finance",
    "logistics", "maritime_transport", "road_safety", "traffic_management",
    "transport_economics", "future_of_mobility", "cycle_infrastructure",
    
    # Communication & Media
    "media_ethics", "journalism", "social_media_impact", "digital_literacy",
    "advertising_strategies", "public_relations", "broadcast_media",
    "content_creation", "media_ownership", "news_consumption", "misinformation",
    
    # Energy & Resources
    "nuclear_energy", "geothermal_energy", "tidal_energy", "bioenergy",
    "fossil_fuels", "energy_efficiency", "smart_grids", "energy_storage",
    "critical_minerals", "resource_management", "oil_economics", "mining_ethics",
    
    # Miscellaneous (General Interest)
    "ethics_in_science", "space_ethics", "climate_refugees", "smart_homes",
    "gesture_recognition", "materials_innovation", "lighting_technology",
    "acoustic_engineering", "biometrics", "ergonomics", "futurism",
    "sustainability_in_design", "consumer_rights", "data_protection",
    "privacy_in_digital_age", "labor_rights", "urban_gardening", "ecotourism",
    "adventure_tourism", "volunteerism", "community_development", "philanthropy",
]

# ─── TOPICS BY DIFFICULTY ─────────────────────────────────────────────
TOPICS_BY_DIFFICULTY = {
    'easy': [
        "biodiversity", "urban_life", "plant_science", "renewable_energy",
        "remote_work", "social_evolution", "food_security", "digital_education",
        "mental_health_awareness", "sustainable_agriculture", "marine_biology",
        "criminal_justice", "fractal_geometry", "economic_development",
        "aging_population", "climate_modeling", "artificial_intelligence",
        "space_exploration", "genetic_research", "historical_archaeology",
        "nanotechnology", "robotics", "cybersecurity", "biotechnology",
        "green_technology", "internet_of_things", "machine_learning",
        "materials_science", "urban_forestry", "waste_management",
        "cultural_heritage", "museum_studies", "architectural_history",
        "public_health", "nutrition_science", "global_trade",
        "entrepreneurship", "consumer_behavior", "educational_technology",
        "child_development", "human_rights", "criminal_justice_system",
        "sports_psychology", "art_therapy", "creative_writing",
        "agricultural_technology", "organic_farming", "urban_transport",
        "public_transport", "media_ethics", "journalism", "social_media_impact",
        "nuclear_energy", "geothermal_energy", "tidal_energy", "ethics_in_science",
        "smart_homes", "gesture_recognition", "biometrics", "ergonomics",
        "futurism", "sustainability_in_design", "consumer_rights", "data_protection",
        "privacy_in_digital_age", "labor_rights", "urban_gardening", "ecotourism",
        "adventure_tourism", "volunteerism", "community_development", "philanthropy"
    ],
    'medium': [
        "bioinformatics", "climate_modeling", "neural_pathways", "protein_folding",
        "fractal_geometry", "criminal_justice", "aging_population",
        "renewable_energy", "genetic_research", "marine_biology",
        "artificial_intelligence", "quantum_computing", "space_exploration",
        "biotechnology", "cryogenics", "dark_matter", "exoplanets",
        "fusion_energy", "gene_editing", "green_technology", "hydrogen_fuel",
        "internet_of_things", "machine_learning", "materials_science",
        "neural_networks", "particle_physics", "photovoltaics", "robotics_ethics",
        "semiconductors", "space_tourism", "sustainable_transport", "telemedicine",
        "virtual_reality", "wearable_technology", "autonomous_vehicles",
        "conservation_biology", "coral_reefs", "deforestation", "desertification",
        "ecological_footprint", "ecosystem_services", "endangered_species",
        "environmental_policy", "green_buildings", "groundwater_depletion",
        "habitat_fragmentation", "marine_ecosystems", "microplastics", "noise_pollution",
        "ozone_depletion", "plastic_recycling", "pollinator_decline", "rainforests",
        "soil_erosion", "urban_forestry", "waste_management", "water_scarcity",
        "wetland_restoration", "wildfire_management", "wind_energy",
        "epidemiology", "nutrition_science", "public_health", "biomedical_engineering",
        "clinical_research", "pharmaceutical_development", "genomic_medicine",
        "infectious_diseases", "mental_health", "neurology", "cardiovascular_health",
        "diabetes_research", "digital_health", "epidemic_response", "exercise_science",
        "food_safety", "global_health", "immunology", "longevity_research",
        "medical_ethics", "neuroplasticity", "obesity_epidemic", "occupational_health",
        "pain_management", "palliative_care", "personalized_medicine", "psychology",
        "public_health_policy", "reproductive_health", "sleep_science", "smoking_cessation",
        "sports_medicine", "stem_cell_research", "telehealth", "vaccination",
        "wellness_industry", "women_health", "youth_health",
        "global_trade", "financial_markets", "entrepreneurship", "supply_chain",
        "business_ethics", "economic_policy", "consumer_behavior", "corporate_governance",
        "e_commerce", "economic_development", "financial_literacy", "foreign_investment",
        "gig_economy", "industrial_policy", "international_business", "marketing_strategies",
        "monetary_policy", "post_covid_economy", "real_estate", "retail_innovation",
        "sustainable_business", "tax_policy", "trade_agreements", "venture_capital",
        "work_automation", "workplace_diversity", "digital_currency", "sharing_economy",
        "green_finance", "corporate_social_responsibility",
        "educational_psychology", "learning_theories", "child_development",
        "cognitive_science", "behavioral_economics", "educational_technology",
        "student_motivation", "curriculum_design", "early_childhood_education",
        "higher_education", "language_acquisition", "lifelong_learning",
        "neuroeducation", "online_learning", "pedagogy", "special_education",
        "teacher_training", "thinking_skills", "vocational_education",
        "human_rights", "constitutional_law", "international_relations",
        "criminal_justice_system", "public_policy", "political_ideologies",
        "diplomacy", "election_systems", "foreign_policy", "governance",
        "judicial_system", "legal_ethics", "peacekeeping", "political_economy",
        "public_administration", "rule_of_law", "security_studies",
        "social_welfare_policy", "political_campaigns", "international_law",
        "sports_psychology", "athletic_performance", "fan_behavior",
        "sports_injuries", "doping_in_sports", "women_in_sports",
        "paralympic_movement", "sports_economics", "recreation_and_leisure",
        "adventure_sports", "team_dynamics", "sports_media", "youth_sports",
        "art_therapy", "creative_writing", "digital_arts", "entertainment_industry",
        "film_production", "music_therapy", "performance_arts", "visual_arts",
        "agricultural_technology", "food_chemistry", "organic_farming",
        "food_security_policy", "aquaculture", "crop_improvement", "dairy_science",
        "food_processing", "horticulture", "livestock_management", "precision_agriculture",
        "soil_health", "sustainable_fisheries", "vertical_farming", "food_waste_reduction",
        "urban_transport", "public_transport", "aviation_technology",
        "railway_development", "smart_cities", "infrastructure_finance",
        "logistics", "maritime_transport", "road_safety", "traffic_management",
        "transport_economics", "future_of_mobility", "cycle_infrastructure",
        "media_ethics", "journalism", "social_media_impact", "digital_literacy",
        "advertising_strategies", "public_relations", "broadcast_media",
        "content_creation", "media_ownership", "news_consumption", "misinformation",
        "nuclear_energy", "geothermal_energy", "tidal_energy", "bioenergy",
        "fossil_fuels", "energy_efficiency", "smart_grids", "energy_storage",
        "critical_minerals", "resource_management", "oil_economics", "mining_ethics",
        "ethics_in_science", "space_ethics", "climate_refugees", "smart_homes",
        "gesture_recognition", "materials_innovation", "lighting_technology",
        "acoustic_engineering", "biometrics", "ergonomics", "futurism",
        "sustainability_in_design", "consumer_rights", "data_protection",
        "privacy_in_digital_age", "labor_rights", "urban_gardening", "ecotourism",
        "adventure_tourism", "volunteerism", "community_development", "philanthropy"
    ],
    'hard': [
        "quantum_computing", "nanotechnology", "genetic_research",
        "space_exploration", "historical_archaeology", "economic_development",
        "cryogenics", "dark_matter", "exoplanets", "fusion_energy",
        "gene_editing", "neural_networks", "particle_physics", "photovoltaics",
        "semiconductors", "telemedicine", "virtual_reality", "autonomous_vehicles",
        "mathematical_biology", "string_theory", "quantum_gravity",
        "astrobiology", "neuroscience", "ethology", "bioethics", "geomicrobiology",
        "paleoclimatology", "seismology", "volcanology", "geochemistry",
        "biomechanics", "psycholinguistics", "anthropology", "sociolinguistics",
        "applied_ethics", "philosophy_of_science", "political_philosophy",
        "international_political_economy", "public_policy_analysis",
        "strategic_studies", "conflict_resolution", "diplomatic_history",
        "global_governance", "human_security", "environmental_justice",
        "climate_policy", "energy_geopolitics", "sustainable_development_goals",
        "blue_economy", "circular_economy", "green_chemistry", "industrial_ecology",
        "complexity_science", "network_theory", "chaos_theory", "systems_biology",
        "computational_linguistics", "cognitive_linguistics", "neurolinguistics",
        "archaeogenetics", "paleoanthropology", "cultural_evolution", "museology",
        "curatorial_studies", "art_conservation", "architectural_engineering",
        "urban_design", "landscape_architecture", "transport_engineering",
        "aerospace_engineering", "materials_engineering", "biomedical_engineering",
        "chemical_engineering", "environmental_engineering", "software_engineering",
        "data_science", "artificial_intelligence_ethics", "robot_ethics",
        "digital_humanities", "computational_social_science", "e_research",
        "scholarly_communication", "open_science", "science_policy",
        "technology_management", "innovation_studies", "research_methodology"
    ]
}

# ─── ALL TOPICS (Merged Set) ──────────────────────────────────────────
ALL_TOPICS = list(set(
    DEFAULT_TOPICS +
    TOPICS_BY_DIFFICULTY.get('easy', []) +
    TOPICS_BY_DIFFICULTY.get('medium', []) +
    TOPICS_BY_DIFFICULTY.get('hard', [])
))

# Total count for reference
TOPIC_COUNT = len(ALL_TOPICS)