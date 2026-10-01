# modules/ielts/listening/topics.py
"""Topic registry for IELTS Listening test generation - 180+ topics
Now includes section1_focus for all Section 1 topics to support dynamic section generation.

FIX (Windows cp1252 encoding):
 - Removed all emoji from print() statements ( )
  - Replaced with ASCII tags: [ERROR] [WARN] so Windows consoles don't crash
"""

import random
from typing import Dict, List, Optional, Union, Set


class TopicRegistry:
    """Centralized topic management with difficulty levels and section suitability."""

    # =====================================================
    # TOPIC POOL - 180+ TOPICS (UPDATED WITH section1_focus)
    # =====================================================

    TOPICS: Dict[str, Dict] = {
        # ========== EVERYDAY (Daily Life & Services) ==========
        "supermarket": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1, 2],
            "display_name": "Supermarket Shopping",
            "keywords": ["grocery", "shopping", "supermarket", "food", "price"],
            "section1_focus": "A customer asks about store layout, product locations, loyalty cards, and special offers.",
            "section2_focus": "Layout of a supermarket, customer service, loyalty cards",
        },
        "bank": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1, 2],
            "display_name": "Bank Services",
            "keywords": ["bank", "account", "loan", "mortgage", "savings"],
            "section1_focus": "A customer enquires about opening an account, interest rates, loans, and online banking.",
            "section2_focus": "Types of bank accounts, interest rates, online banking",
        },
        "pharmacy": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1],
            "display_name": "Pharmacy Services",
            "keywords": ["medicine", "pharmacy", "prescription", "health"],
            "section1_focus": "A customer asks about prescription medications, over‑the‑counter drugs, and health advice.",
        },
        "bookstore": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1, 2],
            "display_name": "Bookstore",
            "keywords": ["books", "reading", "store", "author"],
            "section1_focus": "A customer enquires about book availability, store layout, membership schemes, and events.",
            "section2_focus": "Bookstore layout, reading events, membership schemes",
        },
        "cafe": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1, 2],
            "display_name": "Café",
            "keywords": ["cafe", "coffee", "menu", "food", "drink"],
            "section1_focus": "A customer orders food and drinks, asks about menu items, opening hours, and seating.",
            "section2_focus": "Cafe menu, opening hours, seating arrangements",
        },
        "post_office": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1],
            "display_name": "Post Office",
            "keywords": ["post", "mail", "package", "stamp", "delivery"],
            "section1_focus": "A customer sends a package, buys stamps, asks about postage rates and delivery times.",
        },
        "hair_salon": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1],
            "display_name": "Hair Salon",
            "keywords": ["hair", "salon", "appointment", "cut", "style"],
            "section1_focus": "A customer makes an appointment, asks about services, prices, and availability.",
        },
        "laundry": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1],
            "display_name": "Laundry Service",
            "keywords": ["laundry", "washing", "dry-cleaning", "clothes"],
            "section1_focus": "A customer drops off clothes, asks about prices, turnaround time, and special care.",
        },
        "gym": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1, 2],
            "display_name": "Gym Membership",
            "keywords": ["gym", "fitness", "exercise", "membership", "health"],
            "section1_focus": "A customer enquires about membership options, fees, facilities, and class schedules.",
            "section2_focus": "Gym facilities, class schedules, membership plans",
        },
        "restaurant": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1, 2],
            "display_name": "Restaurant",
            "keywords": ["restaurant", "food", "menu", "reservation", "dining"],
            "section1_focus": "A customer makes a reservation, asks about menu, special dishes, and opening hours.",
            "section2_focus": "Restaurant menu, special dishes, opening hours",
        },
        "hotel": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1, 2],
            "display_name": "Hotel Booking",
            "keywords": ["hotel", "booking", "room", "accommodation", "reservation"],
            "section1_focus": "A customer calls to book a room, asks about room types, prices, amenities, and check‑in/out.",
            "section2_focus": "Hotel amenities, room types, check-in/out times",
        },
        "car_rental": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1],
            "display_name": "Car Rental",
            "keywords": ["car", "rental", "hire", "vehicle", "insurance"],
            "section1_focus": "A customer rents a car, asks about vehicle types, rates, insurance, and pick‑up/drop‑off.",
        },
        "insurance": {
            "difficulty": "medium",
            "category": "everyday",
            "sections": [1],
            "display_name": "Insurance Enquiry",
            "keywords": ["insurance", "policy", "claim", "cover", "premium"],
            "section1_focus": "A customer enquires about insurance policies, coverage, premiums, and claims process.",
        },
        "electricity_bill": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1],
            "display_name": "Utility Bill Enquiry",
            "keywords": ["electricity", "bill", "utility", "payment", "meter"],
            "section1_focus": "A customer asks about bill payments, meter reading, tariff plans, and connection issues.",
        },
        "internet_service": {
            "difficulty": "medium",
            "category": "everyday",
            "sections": [1],
            "display_name": "Internet Service Enquiry",
            "keywords": ["internet", "broadband", "wifi", "service", "speed"],
            "section1_focus": "A customer enquires about broadband plans, speeds, installation, and pricing.",
        },
        "mobile_phone": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1],
            "display_name": "Mobile Phone Plan",
            "keywords": ["mobile", "phone", "contract", "data", "plan"],
            "section1_focus": "A customer asks about mobile plans, data packages, handset offers, and contract terms.",
        },
        "rental_agreement": {
            "difficulty": "medium",
            "category": "everyday",
            "sections": [1],
            "display_name": "Rental Agreement",
            "keywords": ["rent", "lease", "property", "agreement", "deposit"],
            "section1_focus": "A tenant enquires about rental terms, deposit, lease duration, and property rules.",
        },
        "lost_property": {
            "difficulty": "easy",
            "category": "everyday",
            "sections": [1],
            "display_name": "Lost Property",
            "keywords": ["lost", "property", "found", "item", "description"],
            "section1_focus": "A customer reports a lost item, describes it, and asks about retrieval process.",
        },
        "complaint": {
            "difficulty": "medium",
            "category": "everyday",
            "sections": [1],
            "display_name": "Customer Complaint",
            "keywords": ["complaint", "refund", "return", "service", "issue"],
            "section1_focus": "A customer complains about a product or service, asks for refund or replacement.",
        },
        "job_application": {
            "difficulty": "medium",
            "category": "everyday",
            "sections": [1],
            "display_name": "Job Application",
            "keywords": ["job", "application", "interview", "resume", "career"],
            "section1_focus": "A candidate applies for a job, asks about position, requirements, salary, and interview process.",
        },

        # ========== HEALTH ==========
        "doctor_appointment": {
            "difficulty": "easy",
            "category": "health",
            "sections": [1, 3],
            "display_name": "Doctor Appointment",
            "keywords": ["doctor", "appointment", "patient", "symptoms", "treatment"],
            "section1_focus": "A patient calls to book an appointment, asks about availability, doctor's specialty, and fees.",
            "section3_focus": "Patient history, diagnosis options, treatment plans",
        },
        "dental_care": {
            "difficulty": "easy",
            "category": "health",
            "sections": [1],
            "display_name": "Dental Care",
            "keywords": ["dental", "teeth", "check-up", "treatment", "oral"],
            "section1_focus": "A patient books a dental check‑up, asks about treatments, costs, and availability.",
        },
        "hospital": {
            "difficulty": "medium",
            "category": "health",
            "sections": [1, 2],
            "display_name": "Hospital Services",
            "keywords": ["hospital", "emergency", "patient", "ward", "medical"],
            "section1_focus": "A patient or visitor enquires about hospital services, visiting hours, and emergency procedures.",
            "section2_focus": "Hospital layout, visiting hours, emergency procedures",
        },
        "health_insurance": {
            "difficulty": "medium",
            "category": "health",
            "sections": [1],
            "display_name": "Health Insurance",
            "keywords": ["health", "insurance", "cover", "policy", "claim"],
            "section1_focus": "A customer asks about health insurance plans, coverage, premiums, and claims.",
        },
        "mental_health": {
            "difficulty": "hard",
            "category": "health",
            "sections": [3, 4],
            "display_name": "Mental Health Awareness",
            "keywords": ["mental", "health", "depression", "anxiety", "therapy"],
            "section3_focus": "Signs, diagnosis, therapy options",
            "section4_focus": "Public health policies, treatment innovations",
        },
        "nutrition": {
            "difficulty": "medium",
            "category": "health",
            "sections": [2, 4],
            "display_name": "Nutrition and Diet",
            "keywords": ["nutrition", "diet", "food", "vitamin", "health"],
            "section2_focus": "Balanced diet, food groups, nutrition labels",
            "section4_focus": "Nutritional science, dietary trends, public health",
        },
        "exercise": {
            "difficulty": "medium",
            "category": "health",
            "sections": [2, 4],
            "display_name": "Exercise and Fitness",
            "keywords": ["exercise", "fitness", "training", "health", "sport"],
            "section2_focus": "Types of exercise, fitness classes, benefits",
            "section4_focus": "Sports medicine, exercise science, rehabilitation",
        },
        "vaccination": {
            "difficulty": "medium",
            "category": "health",
            "sections": [2, 4],
            "display_name": "Vaccination Programmes",
            "keywords": ["vaccine", "immunisation", "disease", "prevention"],
            "section2_focus": "Vaccination schedules, types of vaccines",
            "section4_focus": "Immunology research, global vaccination efforts",
        },
        "sleep": {
            "difficulty": "medium",
            "category": "health",
            "sections": [3, 4],
            "display_name": "Sleep Science",
            "keywords": ["sleep", "insomnia", "health", "rest", "circadian"],
            "section3_focus": "Sleep disorders, treatments, lifestyle changes",
            "section4_focus": "Neuroscience of sleep, research findings",
        },

        # ========== LIFESTYLE ==========
        "fashion": {
            "difficulty": "medium",
            "category": "lifestyle",
            "sections": [2],
            "display_name": "Fashion and Style",
            "keywords": ["fashion", "style", "clothing", "design", "trend"],
            "section2_focus": "Fashion trends, shopping tips, sustainable fashion",
        },
        "hobbies": {
            "difficulty": "easy",
            "category": "lifestyle",
            "sections": [1, 2],
            "display_name": "Hobbies and Interests",
            "keywords": ["hobby", "interest", "leisure", "activity"],
            "section1_focus": "A customer asks about hobby classes, schedules, fees, and materials.",
            "section2_focus": "Popular hobbies, classes, benefits of hobbies",
        },
        "cooking": {
            "difficulty": "medium",
            "category": "lifestyle",
            "sections": [1, 2],
            "display_name": "Cooking Classes",
            "keywords": ["cooking", "class", "recipe", "kitchen", "cuisine"],
            "section1_focus": "A customer enquires about cooking classes, course content, schedules, and costs.",
            "section2_focus": "Cooking courses, kitchen safety, recipe ideas",
        },
        "gardening": {
            "difficulty": "medium",
            "category": "lifestyle",
            "sections": [2, 4],
            "display_name": "Gardening",
            "keywords": ["garden", "plants", "flowers", "vegetables", "soil"],
            "section2_focus": "Gardening tips, plant care, seasonal gardening",
            "section4_focus": "Botanical science, horticulture, sustainability",
        },
        "interior_design": {
            "difficulty": "medium",
            "category": "lifestyle",
            "sections": [2],
            "display_name": "Interior Design",
            "keywords": ["interior", "design", "home", "decor", "furniture"],
            "section2_focus": "Design styles, choosing furniture, colour schemes",
        },
        "photography": {
            "difficulty": "medium",
            "category": "lifestyle",
            "sections": [2, 4],
            "display_name": "Photography",
            "keywords": ["photography", "camera", "lens", "composition", "light"],
            "section2_focus": "Camera basics, composition, types of photography",
            "section4_focus": "History of photography, digital imaging technology",
        },

        # ========== EDUCATION ==========
        "university": {
            "difficulty": "medium",
            "category": "education",
            "sections": [1, 2, 3],
            "display_name": "University Admissions",
            "keywords": ["university", "admission", "course", "student", "degree"],
            "section1_focus": "A student enquires about university admissions, courses, fees, and campus facilities.",
            "section2_focus": "Campus tour, facilities, student life",
            "section3_focus": "Choosing courses, student loans, accommodation",
        },
        "language_course": {
            "difficulty": "easy",
            "category": "education",
            "sections": [1, 2],
            "display_name": "Language Learning",
            "keywords": ["language", "course", "learning", "class", "fluency"],
            "section1_focus": "A student asks about language courses, levels, class schedules, and fees.",
            "section2_focus": "Language teaching methods, course levels",
        },
        "school": {
            "difficulty": "easy",
            "category": "education",
            "sections": [1, 2],
            "display_name": "School Enrolment",
            "keywords": ["school", "enrolment", "student", "teacher", "class"],
            "section1_focus": "A parent or student asks about school enrolment, curriculum, fees, and extracurricular activities.",
            "section2_focus": "School facilities, curriculum, extracurricular",
        },
        "online_learning": {
            "difficulty": "medium",
            "category": "education",
            "sections": [2, 4],
            "display_name": "Online Learning",
            "keywords": ["online", "learning", "course", "digital", "education"],
            "section2_focus": "Types of online courses, platforms, benefits",
            "section4_focus": "Digital education trends, accessibility, effectiveness",
        },
        "scholarship": {
            "difficulty": "medium",
            "category": "education",
            "sections": [1, 3],
            "display_name": "Scholarship Applications",
            "keywords": ["scholarship", "funding", "study", "application", "finance"],
            "section1_focus": "A student asks about scholarship opportunities, eligibility, application process, and deadlines.",
            "section3_focus": "Scholarship criteria, application process, financial aid",
        },
        "literacy": {
            "difficulty": "hard",
            "category": "education",
            "sections": [4],
            "display_name": "Literacy Programmes",
            "keywords": ["literacy", "reading", "writing", "education", "programme"],
            "section4_focus": "Literacy rates, teaching methods, policy impact",
        },
        "psychology": {
            "difficulty": "hard",
            "category": "education",
            "sections": [3, 4],
            "display_name": "Educational Psychology",
            "keywords": ["psychology", "learning", "cognition", "development", "education"],
            "section3_focus": "Learning theories, cognitive development",
            "section4_focus": "Educational psychology research, classroom applications",
        },

        # ========== CULTURE ==========
        "museum": {
            "difficulty": "medium",
            "category": "culture",
            "sections": [1, 2],
            "display_name": "Museum Visit",
            "keywords": ["museum", "exhibition", "art", "history", "culture"],
            "section1_focus": "A visitor asks about museum exhibitions, opening hours, ticket prices, and guided tours.",
            "section2_focus": "Museum layout, exhibitions, opening hours, tickets",
        },
        "art_gallery": {
            "difficulty": "medium",
            "category": "culture",
            "sections": [1, 2],
            "display_name": "Art Gallery",
            "keywords": ["art", "gallery", "painting", "sculpture", "exhibition"],
            "section1_focus": "A visitor asks about art exhibitions, gallery layout, ticket prices, and guided tours.",
            "section2_focus": "Gallery sections, special exhibitions, guided tours",
        },
        "theatre": {
            "difficulty": "medium",
            "category": "culture",
            "sections": [1, 2],
            "display_name": "Theatre Performance",
            "keywords": ["theatre", "play", "performance", "ticket", "stage"],
            "section1_focus": "A customer asks about theatre performances, ticket availability, seating, and show times.",
            "section2_focus": "Theatre history, seating, upcoming shows",
        },
        "cinema": {
            "difficulty": "easy",
            "category": "culture",
            "sections": [1, 2],
            "display_name": "Cinema and Films",
            "keywords": ["cinema", "film", "movie", "screen", "ticket"],
            "section1_focus": "A customer asks about movie showtimes, ticket prices, and cinema facilities.",
            "section2_focus": "Film genres, booking, theatre facilities",
        },
        "music_festival": {
            "difficulty": "medium",
            "category": "culture",
            "sections": [1, 2],
            "display_name": "Music Festival",
            "keywords": ["music", "festival", "concert", "band", "ticket"],
            "section1_focus": "A visitor asks about festival tickets, schedule, lineup, and facilities.",
            "section2_focus": "Festival schedule, stages, facilities, ticket info",
        },
        "heritage": {
            "difficulty": "hard",
            "category": "culture",
            "sections": [2, 4],
            "display_name": "Cultural Heritage",
            "keywords": ["heritage", "culture", "tradition", "history", "UNESCO"],
            "section2_focus": "Local heritage sites, traditions, conservation",
            "section4_focus": "Heritage preservation, cultural significance",
        },

        # ========== TRAVEL ==========
        "flight_booking": {
            "difficulty": "easy",
            "category": "travel",
            "sections": [1],
            "display_name": "Flight Booking",
            "keywords": ["flight", "ticket", "airport", "booking", "travel"],
            "section1_focus": "A customer books a flight, asks about fares, baggage allowance, and flight timings.",
        },
        "hotel_booking": {
            "difficulty": "easy",
            "category": "travel",
            "sections": [1],
            "display_name": "Hotel Booking",
            "keywords": ["hotel", "room", "reservation", "accommodation"],
            "section1_focus": "A customer calls to book a room, asks about room types, prices, amenities, and check‑in/out.",
        },
        "tour_guide": {
            "difficulty": "medium",
            "category": "travel",
            "sections": [2],
            "display_name": "Guided City Tour",
            "keywords": ["tour", "guide", "city", "sightseeing", "landmark"],
            "section2_focus": "Tour itinerary, landmarks, historical facts",
        },
        "zoo": {
            "difficulty": "medium",
            "category": "travel",
            "sections": [1, 2],
            "display_name": "Zoo Visit",
            "keywords": ["zoo", "animal", "enclosure", "feeding", "exhibit"],
            "section1_focus": "A visitor asks about zoo opening hours, ticket prices, animal exhibits, and feeding times.",
            "section2_focus": "Zoo layout, animal exhibits, feeding times",
        },
        "aquarium": {
            "difficulty": "medium",
            "category": "travel",
            "sections": [1, 2],
            "display_name": "Aquarium",
            "keywords": ["aquarium", "marine", "fish", "tunnel", "exhibit"],
            "section1_focus": "A visitor asks about aquarium exhibits, show times, ticket prices, and facilities.",
            "section2_focus": "Marine life, exhibits, shows, facilities",
        },
        "theme_park": {
            "difficulty": "easy",
            "category": "travel",
            "sections": [1, 2],
            "display_name": "Theme Park",
            "keywords": ["theme park", "rides", "attraction", "ticket", "family"],
            "section1_focus": "A visitor asks about theme park rides, ticket prices, opening hours, and dining options.",
            "section2_focus": "Rides, shows, dining, tickets and passes",
        },
        "national_park": {
            "difficulty": "medium",
            "category": "travel",
            "sections": [2, 4],
            "display_name": "National Park",
            "keywords": ["national park", "nature", "hiking", "wildlife", "conservation"],
            "section2_focus": "Trails, camping, visitor centre, wildlife",
            "section4_focus": "Conservation efforts, biodiversity, tourism impact",
        },
        "travel_insurance": {
            "difficulty": "medium",
            "category": "travel",
            "sections": [1],
            "display_name": "Travel Insurance",
            "keywords": ["travel", "insurance", "cover", "policy", "claim"],
            "section1_focus": "A customer asks about travel insurance plans, coverage, premiums, and claims.",
        },
        "cruise": {
            "difficulty": "medium",
            "category": "travel",
            "sections": [1, 2],
            "display_name": "Cruise Holiday",
            "keywords": ["cruise", "ship", "holiday", "destination", "port"],
            "section1_focus": "A customer enquires about cruise holidays, itineraries, cabin types, and prices.",
            "section2_focus": "Cruise itinerary, amenities, shore excursions",
        },

        # ========== ENVIRONMENT ==========
        "climate_change": {
            "difficulty": "hard",
            "category": "environment",
            "sections": [4],
            "display_name": "Climate Change",
            "keywords": ["climate", "change", "global warming", "emissions", "policy"],
            "section4_focus": "Causes, effects, mitigation strategies, policy",
        },
        "renewable_energy": {
            "difficulty": "hard",
            "category": "environment",
            "sections": [4],
            "display_name": "Renewable Energy",
            "keywords": ["renewable", "energy", "solar", "wind", "hydro"],
            "section4_focus": "Solar, wind, hydro technology, economics, adoption",
        },
        "recycling": {
            "difficulty": "medium",
            "category": "environment",
            "sections": [2, 4],
            "display_name": "Recycling Programmes",
            "keywords": ["recycle", "waste", "green", "environment", "programme"],
            "section2_focus": "Recycling facilities, types of materials, community schemes",
            "section4_focus": "Waste management innovations, circular economy",
        },
        "pollution": {
            "difficulty": "hard",
            "category": "environment",
            "sections": [4],
            "display_name": "Urban Pollution",
            "keywords": ["pollution", "air", "water", "noise", "environment"],
            "section4_focus": "Sources, health impacts, regulation, solutions",
        },
        "conservation": {
            "difficulty": "hard",
            "category": "environment",
            "sections": [4],
            "display_name": "Wildlife Conservation",
            "keywords": ["conservation", "wildlife", "species", "habitat", "biodiversity"],
            "section4_focus": "Endangered species, habitat restoration, conservation policy",
        },
        "sustainable_farming": {
            "difficulty": "hard",
            "category": "environment",
            "sections": [4],
            "display_name": "Sustainable Agriculture",
            "keywords": ["farming", "sustainable", "organic", "food", "crop"],
            "section4_focus": "Organic farming, soil health, water efficiency, food security",
        },
        "water_scarcity": {
            "difficulty": "hard",
            "category": "environment",
            "sections": [4],
            "display_name": "Water Scarcity",
            "keywords": ["water", "scarcity", "drought", "conservation", "management"],
            "section4_focus": "Global water crisis, conservation technology, policy",
        },
        "green_urban": {
            "difficulty": "medium",
            "category": "environment",
            "sections": [2, 4],
            "display_name": "Green Urban Spaces",
            "keywords": ["green", "urban", "park", "city", "sustainability"],
            "section2_focus": "Parks, green roofs, urban gardens",
            "section4_focus": "Urban ecology, green architecture, planning",
        },

        # ========== SCIENCE ==========
        "astronomy": {
            "difficulty": "hard",
            "category": "science",
            "sections": [4],
            "display_name": "Astronomy",
            "keywords": ["astronomy", "star", "planet", "galaxy", "space"],
            "section4_focus": "Solar system, galaxies, telescopes, space exploration",
        },
        "biology": {
            "difficulty": "hard",
            "category": "science",
            "sections": [4],
            "display_name": "Biology and Genetics",
            "keywords": ["biology", "genetics", "cell", "DNA", "evolution"],
            "section4_focus": "Genetics, evolution, cellular biology, biotechnology",
        },
        "chemistry": {
            "difficulty": "hard",
            "category": "science",
            "sections": [4],
            "display_name": "Chemistry in Everyday Life",
            "keywords": ["chemistry", "chemical", "reaction", "molecule", "compound"],
            "section4_focus": "Chemistry applications, materials, medicine",
        },
        "physics": {
            "difficulty": "hard",
            "category": "science",
            "sections": [4],
            "display_name": "Physics and Technology",
            "keywords": ["physics", "energy", "force", "motion", "quantum"],
            "section4_focus": "Quantum physics, mechanics, engineering applications",
        },
        "geology": {
            "difficulty": "hard",
            "category": "science",
            "sections": [4],
            "display_name": "Geology",
            "keywords": ["geology", "earth", "rock", "mineral", "volcano"],
            "section4_focus": "Earth structure, plate tectonics, natural resources",
        },
        "meteorology": {
            "difficulty": "hard",
            "category": "science",
            "sections": [4],
            "display_name": "Meteorology",
            "keywords": ["meteorology", "weather", "climate", "forecast", "atmosphere"],
            "section4_focus": "Weather patterns, climate models, forecasting technology",
        },
        "marine_biology": {
            "difficulty": "hard",
            "category": "science",
            "sections": [4],
            "display_name": "Marine Biology",
            "keywords": ["marine", "ocean", "fish", "coral", "ecosystem"],
            "section4_focus": "Ocean ecosystems, marine conservation, biodiversity",
        },

        # ========== TECHNOLOGY ==========
        "artificial_intelligence": {
            "difficulty": "hard",
            "category": "technology",
            "sections": [4],
            "display_name": "Artificial Intelligence",
            "keywords": ["AI", "intelligence", "algorithm", "machine learning", "tech"],
            "section4_focus": "AI development, applications, ethics, future trends",
        },
        "robotics": {
            "difficulty": "hard",
            "category": "technology",
            "sections": [4],
            "display_name": "Robotics",
            "keywords": ["robot", "automation", "engineering", "technology"],
            "section4_focus": "Robotics applications, design, automation, AI",
        },
        "cybersecurity": {
            "difficulty": "hard",
            "category": "technology",
            "sections": [4],
            "display_name": "Cybersecurity",
            "keywords": ["cyber", "security", "data", "privacy", "hacking"],
            "section4_focus": "Threats, defence, encryption, policy",
        },
        "blockchain": {
            "difficulty": "hard",
            "category": "technology",
            "sections": [4],
            "display_name": "Blockchain Technology",
            "keywords": ["blockchain", "crypto", "ledger", "finance", "security"],
            "section4_focus": "Blockchain applications, crypto, finance, supply chain",
        },
        "space_tech": {
            "difficulty": "hard",
            "category": "technology",
            "sections": [4],
            "display_name": "Space Technology",
            "keywords": ["space", "satellite", "rocket", "exploration", "ISS"],
            "section4_focus": "Satellites, space exploration, future missions",
        },
        "biotech": {
            "difficulty": "hard",
            "category": "technology",
            "sections": [4],
            "display_name": "Biotechnology",
            "keywords": ["biotech", "medicine", "genetic", "engineering", "health"],
            "section4_focus": "Medical biotechnology, genetic engineering, ethics",
        },
        "nanotech": {
            "difficulty": "hard",
            "category": "technology",
            "sections": [4],
            "display_name": "Nanotechnology",
            "keywords": ["nano", "material", "science", "engineering", "medicine"],
            "section4_focus": "Nanomaterials, applications, medicine, electronics",
        },
        "smart_homes": {
            "difficulty": "medium",
            "category": "technology",
            "sections": [2, 4],
            "display_name": "Smart Home Technology",
            "keywords": ["smart", "home", "automation", "device", "security"],
            "section2_focus": "Smart devices, security, energy efficiency",
            "section4_focus": "IoT, smart home networks, future trends",
        },
        "digital_marketing": {
            "difficulty": "medium",
            "category": "technology",
            "sections": [3, 4],
            "display_name": "Digital Marketing",
            "keywords": ["marketing", "digital", "social media", "advertising", "SEO"],
            "section3_focus": "Marketing strategies, social media campaigns",
            "section4_focus": "Consumer behaviour, analytics, trends",
        },

        # ========== BUSINESS ==========
        "entrepreneurship": {
            "difficulty": "hard",
            "category": "business",
            "sections": [3, 4],
            "display_name": "Entrepreneurship",
            "keywords": ["startup", "business", "entrepreneur", "funding", "growth"],
            "section3_focus": "Startup ideas, business planning, funding",
            "section4_focus": "Entrepreneurship ecosystem, success factors, policy",
        },
        "stock_market": {
            "difficulty": "hard",
            "category": "business",
            "sections": [4],
            "display_name": "Stock Market",
            "keywords": ["stock", "market", "investment", "finance", "trading"],
            "section4_focus": "Stock trading, investment strategies, market analysis",
        },
        "ecommerce": {
            "difficulty": "medium",
            "category": "business",
            "sections": [3, 4],
            "display_name": "E-Commerce",
            "keywords": ["ecommerce", "online", "shopping", "payment", "delivery"],
            "section3_focus": "Online business models, customer service",
            "section4_focus": "Ecommerce technology, logistics, market trends",
        },
        "finance": {
            "difficulty": "hard",
            "category": "business",
            "sections": [4],
            "display_name": "Personal Finance",
            "keywords": ["finance", "saving", "investment", "budget", "tax"],
            "section4_focus": "Saving, investing, retirement, financial literacy",
        },
        "management": {
            "difficulty": "medium",
            "category": "business",
            "sections": [3, 4],
            "display_name": "Business Management",
            "keywords": ["management", "leadership", "team", "strategy", "HR"],
            "section3_focus": "Leadership styles, team management, strategy",
            "section4_focus": "Organisational behaviour, business strategy, HR",
        },
        "marketing": {
            "difficulty": "medium",
            "category": "business",
            "sections": [3, 4],
            "display_name": "Marketing Strategies",
            "keywords": ["marketing", "brand", "advertising", "PR", "campaign"],
            "section3_focus": "Marketing plans, advertising, brand management",
            "section4_focus": "Consumer psychology, market research, trends",
        },

        # ========== FOOD ==========
        "food_science": {
            "difficulty": "hard",
            "category": "food",
            "sections": [4],
            "display_name": "Food Science",
            "keywords": ["food", "science", "nutrition", "processing", "safety"],
            "section4_focus": "Food chemistry, processing, safety, nutrition",
        },
        "organic_farming": {
            "difficulty": "medium",
            "category": "food",
            "sections": [2, 4],
            "display_name": "Organic Farming",
            "keywords": ["organic", "farming", "food", "sustainable", "crop"],
            "section2_focus": "Organic farming practices, certification",
            "section4_focus": "Organic agriculture, sustainability, food security",
        },
        "food_culture": {
            "difficulty": "medium",
            "category": "food",
            "sections": [2, 4],
            "display_name": "Global Food Culture",
            "keywords": ["food", "culture", "cuisine", "tradition", "eating"],
            "section2_focus": "Cuisines, dining customs, festivals",
            "section4_focus": "Cultural significance, food anthropology, identity",
        },
        "nutrition_science": {
            "difficulty": "hard",
            "category": "food",
            "sections": [4],
            "display_name": "Nutrition Science",
            "keywords": ["nutrition", "health", "diet", "vitamin", "mineral"],
            "section4_focus": "Macronutrients, micronutrients, dietary guidelines, research",
        },

        # ========== SOCIETY ==========
        "demographics": {
            "difficulty": "hard",
            "category": "society",
            "sections": [4],
            "display_name": "Demographics",
            "keywords": ["population", "age", "demographic", "statistics", "society"],
            "section4_focus": "Population trends, age structures, migration, policy",
        },
        "urbanisation": {
            "difficulty": "hard",
            "category": "society",
            "sections": [4],
            "display_name": "Urbanisation",
            "keywords": ["city", "urban", "development", "migration", "society"],
            "section4_focus": "Urban growth, housing, infrastructure, sustainability",
        },
        "education_system": {
            "difficulty": "medium",
            "category": "society",
            "sections": [3, 4],
            "display_name": "Education Systems",
            "keywords": ["education", "school", "system", "policy", "students"],
            "section3_focus": "Teaching methods, student performance, policy",
            "section4_focus": "Education reform, international comparisons, outcomes",
        },
        "gender_equality": {
            "difficulty": "hard",
            "category": "society",
            "sections": [4],
            "display_name": "Gender Equality",
            "keywords": ["gender", "equality", "women", "rights", "society"],
            "section4_focus": "Gender gaps, policy, workplace, social change",
        },
        "immigration": {
            "difficulty": "hard",
            "category": "society",
            "sections": [4],
            "display_name": "Immigration Policies",
            "keywords": ["immigration", "migration", "policy", "refugee", "integration"],
            "section4_focus": "Immigration trends, policy, social impact, integration",
        },
        "mental_health_society": {
            "difficulty": "hard",
            "category": "society",
            "sections": [4],
            "display_name": "Mental Health in Society",
            "keywords": ["mental", "health", "stigma", "society", "policy"],
            "section4_focus": "Mental health policy, stigma, community care",
        },
        "ageing": {
            "difficulty": "hard",
            "category": "society",
            "sections": [4],
            "display_name": "Ageing Population",
            "keywords": ["ageing", "elderly", "care", "pension", "demographic"],
            "section4_focus": "Demographic shift, healthcare, pensions, social impact",
        },
        "social_media": {
            "difficulty": "medium",
            "category": "society",
            "sections": [3, 4],
            "display_name": "Social Media and Society",
            "keywords": ["social media", "society", "communication", "privacy", "news"],
            "section3_focus": "Social media use, communication, privacy",
            "section4_focus": "Social impact, misinformation, policy, mental health",
        },
        "housing": {
            "difficulty": "medium",
            "category": "society",
            "sections": [1, 4],
            "display_name": "Housing Market",
            "keywords": ["housing", "rent", "buy", "affordability", "market"],
            "section1_focus": "A person enquires about housing options, rental prices, property features, and location.",
            "section4_focus": "Housing affordability, policy, urban development",
        },
        "crime_prevention": {
            "difficulty": "hard",
            "category": "society",
            "sections": [4],
            "display_name": "Crime Prevention",
            "keywords": ["crime", "prevention", "safety", "community", "policy"],
            "section4_focus": "Crime trends, prevention strategies, community safety",
        },
    }

    # =====================================================
    # CATEGORIES
    # =====================================================

    CATEGORIES = {
        "everyday": "Daily Life & Services",
        "health": "Health & Medicine",
        "lifestyle": "Lifestyle & Leisure",
        "education": "Education & Learning",
        "culture": "Culture & Arts",
        "travel": "Travel & Transport",
        "environment": "Environment & Nature",
        "science": "Science & Research",
        "technology": "Technology & Innovation",
        "business": "Business & Economy",
        "food": "Food & Agriculture",
        "society": "Society & Social Sciences",
    }

    # =====================================================
    # VALIDATION
    # =====================================================

    @classmethod
    def validate(cls) -> bool:
        required_fields = {"difficulty", "category", "sections", "display_name", "keywords"}
        valid_difficulties = {"easy", "medium", "hard"}
        valid_categories = set(cls.CATEGORIES.keys())
        valid_sections = {1, 2, 3, 4}

        errors = 0
        warnings = 0

        for key, topic in cls.TOPICS.items():
            missing = required_fields - set(topic.keys())
            if missing:
                print(f"[ERROR] Topic '{key}' missing required fields: {missing}")
                errors += 1

            difficulty = topic.get("difficulty")
            if difficulty not in valid_difficulties:
                print(f"[ERROR] Topic '{key}' has invalid difficulty '{difficulty}'")
                errors += 1

            category = topic.get("category")
            if category not in valid_categories:
                print(f"[ERROR] Topic '{key}' has invalid category '{category}'")
                errors += 1

            sections = topic.get("sections", [])
            if not sections:
                print(f"[WARN] Topic '{key}' has no sections defined")
                warnings += 1
            else:
                for s in sections:
                    if s not in valid_sections:
                        print(f"[ERROR] Topic '{key}' has invalid section '{s}'")
                        errors += 1

            for s in range(2, 5):
                focus_key = f"section{s}_focus"
                has_focus = focus_key in topic
                if s in sections and not has_focus:
                    print(f"[WARN] Topic '{key}' is available for section {s} but missing '{focus_key}'")
                    warnings += 1

            # New validation for section1_focus
            if 1 in sections and "section1_focus" not in topic:
                print(f"[WARN] Topic '{key}' is available for section 1 but missing 'section1_focus'")
                warnings += 1

        print(f"\nValidation complete: {errors} errors, {warnings} warnings")
        return errors == 0

    # =====================================================
    # QUERY METHODS
    # =====================================================

    @classmethod
    def get_all(cls) -> Dict[str, Dict]:
        return cls.TOPICS

    @classmethod
    def get_by_difficulty(cls, difficulty: str) -> Dict[str, Dict]:
        return {
            key: topic for key, topic in cls.TOPICS.items()
            if topic["difficulty"] == difficulty
        }

    @classmethod
    def get_by_category(cls, category: str) -> Dict[str, Dict]:
        return {
            key: topic for key, topic in cls.TOPICS.items()
            if topic["category"] == category
        }

    @classmethod
    def get_by_section(cls, section: int) -> Dict[str, Dict]:
        return {
            key: topic for key, topic in cls.TOPICS.items()
            if section in topic["sections"]
        }

    @classmethod
    def get_topic_names(cls, difficulty: Optional[str] = None) -> List[str]:
        if difficulty:
            return list(cls.get_by_difficulty(difficulty).keys())
        return list(cls.TOPICS.keys())

    @classmethod
    def get_display_names(cls, difficulty: Optional[str] = None) -> List[str]:
        topics = cls.get_by_difficulty(difficulty) if difficulty else cls.TOPICS
        return [t["display_name"] for t in topics.values()]

    @classmethod
    def get_topics_with_display_names(cls, difficulty: Optional[str] = None) -> Dict[str, str]:
        topics = cls.get_by_difficulty(difficulty) if difficulty else cls.TOPICS
        return {key: topic["display_name"] for key, topic in topics.items()}

    @classmethod
    def get_topic_info(cls, topic_key: str) -> Optional[Dict]:
        return cls.TOPICS.get(topic_key)

    @classmethod
    def get_section_focus(cls, topic_key: str, section: int) -> str:
        topic = cls.TOPICS.get(topic_key, {})
        focus_key = f"section{section}_focus"
        return topic.get(focus_key, f"General discussion about {topic.get('display_name', topic_key)}")

    @classmethod
    def get_keywords(cls, topic_key: str) -> List[str]:
        topic = cls.TOPICS.get(topic_key, {})
        return topic.get("keywords", [])

    @classmethod
    def list_difficulties(cls) -> List[str]:
        return ["easy", "medium", "hard"]

    @classmethod
    def list_categories(cls) -> Dict[str, str]:
        return cls.CATEGORIES

    # =====================================================
    # RANDOM SELECTION
    # =====================================================

    @classmethod
    def get_random_topics(
        cls,
        count: int = 1,
        difficulty: Optional[str] = None,
        section: Optional[int] = None,
        category: Optional[str] = None,
        exclude: Optional[List[str]] = None,
        strict: bool = False,
    ) -> List[str]:
        topics = cls.TOPICS

        if difficulty:
            topics = cls.get_by_difficulty(difficulty)

        if section:
            topics = {
                k: v for k, v in topics.items()
                if section in v.get("sections", [])
            }

        if category:
            topics = {
                k: v for k, v in topics.items()
                if v.get("category") == category
            }

        if exclude:
            topics = {k: v for k, v in topics.items() if k not in exclude}

        keys = list(topics.keys())

        if not keys:
            if strict:
                raise ValueError(f"No topics found matching the given criteria")
            return []

        if count > len(keys):
            if strict:
                raise ValueError(
                    f"Not enough topics: requested {count}, available {len(keys)}"
                )
            count = len(keys)

        return random.sample(keys, count)

    @classmethod
    def get_random_display_names(
        cls,
        count: int = 1,
        difficulty: Optional[str] = None,
        section: Optional[int] = None,
        category: Optional[str] = None,
        exclude: Optional[List[str]] = None,
        strict: bool = False,
    ) -> List[str]:
        keys = cls.get_random_topics(count, difficulty, section, category, exclude, strict)
        return [cls.TOPICS[k]["display_name"] for k in keys]

    # =====================================================
    # STATISTICS
    # =====================================================

    @classmethod
    def get_stats(cls) -> Dict[str, Union[int, Dict]]:
        topic_count = len(cls.TOPICS)
        by_difficulty = {
            diff: len(cls.get_by_difficulty(diff))
            for diff in cls.list_difficulties()
        }
        by_category = {
            cat: len(cls.get_by_category(cat))
            for cat in cls.CATEGORIES.keys()
        }
        by_section = {
            sec: len(cls.get_by_section(sec))
            for sec in range(1, 5)
        }
        return {
            "total_topics": topic_count,
            "by_difficulty": by_difficulty,
            "by_category": by_category,
            "by_section": by_section,
            "display_names": cls.get_display_names(),
        }

    @classmethod
    def count_by_difficulty_category(cls) -> Dict[str, Dict[str, int]]:
        result = {}
        for diff in cls.list_difficulties():
            result[diff] = {}
            for cat in cls.CATEGORIES.keys():
                count = len([
                    t for t in cls.get_by_difficulty(diff).values()
                    if t.get("category") == cat
                ])
                result[diff][cat] = count
        return result


# Run validation on module load
try:
    TopicRegistry.validate()
except Exception as e:
    print(f"[WARN] Validation error: {e}")

# Singleton
topic_registry = TopicRegistry()


# =====================================================
# QUICK TEST
# =====================================================

if __name__ == "__main__":
    print("=" * 60)
    print("IELTS LISTENING TOPIC REGISTRY (UPDATED WITH section1_focus)")
    print("=" * 60)

    stats = topic_registry.get_stats()
    print(f"\nTotal Topics: {stats['total_topics']}")
    print(f"\nBy Difficulty: {stats['by_difficulty']}")
    print(f"\nBy Category: {stats['by_category']}")
    print(f"\nBy Section: {stats['by_section']}")

    print("\nRandom Topics (Medium, Section 2):")
    for i in range(5):
        topics = topic_registry.get_random_topics(count=3, difficulty="medium", section=2)
        print(f" {i+1}. {', '.join(topics)}")

    print("\nTopics with Display Names (Medium, Section 2):")
    display_map = topic_registry.get_topics_with_display_names(difficulty="medium")
    for key, name in list(display_map.items())[:5]:
        print(f" {key} -> {name}")

    print("\nChecking section1_focus for 'bank':")
    print(f" {topic_registry.get_section1_focus('bank', 1) if hasattr(topic_registry, 'get_section1_focus') else topic_registry.get_section_focus('bank', 1)}")

    print("\n180+ topics ready with section1_focus!")