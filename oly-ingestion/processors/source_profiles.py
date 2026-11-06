# processors/source_profiles.py
"""
Chunk-size profiles and the title → profile map (CLEAN-4: data split out of
processors/chunker.py, which re-exports every name here).

Add every new book / paper title to SOURCE_PROFILE_MAP before ingesting it:
matching is substring-on-title, and an unrecognised title silently falls back
to the 900-token `programming` profile (docs/CORPUS.md lists the profiles).
Web articles never consult the map — ingest_web sizes them with
`SemanticChunker.for_web_article(word_count)`.
"""

from enum import Enum


class SourceProfile(Enum):
    """Chunk sizing profiles keyed to source type."""
    THEORY_HEAVY = "theory_heavy"           # Zatsiorsky, Verkhoshansky
    PROGRAMMING_FOCUSED = "programming"     # Everett, Takano, Pendlay
    DATA_HEAVY_SOVIET = "soviet"            # Medvedev, Laputin & Oleshko
    RESEARCH = "research"                   # Pritchard taper thesis + manuscripts
    WEB_ARTICLE = "web_article"             # Catalyst website, SBS, JTS


# Chunk size and overlap per source profile
CHUNK_PROFILES = {
    SourceProfile.THEORY_HEAVY: {
        "chunk_size": 1100,
        "chunk_overlap": 250,
        "description": "Dense academic prose, multi-paragraph arguments",
    },
    SourceProfile.PROGRAMMING_FOCUSED: {
        "chunk_size": 900,
        "chunk_overlap": 200,
        "description": "Mixed rationale and program descriptions",
    },
    SourceProfile.DATA_HEAVY_SOVIET: {
        "chunk_size": 700,
        "chunk_overlap": 150,
        "description": "Terse prose between data tables",
    },
    SourceProfile.RESEARCH: {
        "chunk_size": 700,     # papers: dense, short paragraphs, numbers throughout
        "chunk_overlap": 150,
        "description": "Journal papers and theses — methods/results prose with numbers",
    },
    SourceProfile.WEB_ARTICLE: {
        "chunk_size": 700,     # default; adjusted dynamically by article length
        "chunk_overlap": 150,
        "description": "Variable — adjusted by article length",
    },
}

# Map known sources to profiles (extend as you ingest new material)
SOURCE_PROFILE_MAP = {
    "Olympic Weightlifting: A Complete Guide": SourceProfile.PROGRAMMING_FOCUSED,
    "Weightlifting Programming": SourceProfile.PROGRAMMING_FOCUSED,
    "Snatch Error Correction Manual": SourceProfile.PROGRAMMING_FOCUSED,
    "Scientific Principles of Hypertrophy Training": SourceProfile.PROGRAMMING_FOCUSED,
    "Intervention": SourceProfile.PROGRAMMING_FOCUSED,
    "Managing the Training of Weightlifters": SourceProfile.DATA_HEAVY_SOVIET,
    "A System of Multi-Year Training in Weightlifting": SourceProfile.DATA_HEAVY_SOVIET,
    "A Program of Multi-Year Training in Weightlifting": SourceProfile.DATA_HEAVY_SOVIET,
    "The Training of the Weightlifter": SourceProfile.DATA_HEAVY_SOVIET,
    "A Textbook on Weightlifting": SourceProfile.THEORY_HEAVY,
    "Programming and Organization of Training": SourceProfile.THEORY_HEAVY,
    "Periodization of Strength Training for Sports": SourceProfile.PROGRAMMING_FOCUSED,
    "Weightlifting Training and Technique": SourceProfile.THEORY_HEAVY,
    "Weightlifting Training and Biomechanics": SourceProfile.THEORY_HEAVY,
    "There Is No System": SourceProfile.THEORY_HEAVY,
    "A De-Masculinization of Strength": SourceProfile.THEORY_HEAVY,
    "Weightlifting, Olympic Style": SourceProfile.PROGRAMMING_FOCUSED,
    "Junior Weightlifting": SourceProfile.PROGRAMMING_FOCUSED,
    "Science and Practice of Strength Training": SourceProfile.THEORY_HEAVY,
    "Supertraining": SourceProfile.THEORY_HEAVY,
    "Weightlifting Encyclopedia": SourceProfile.THEORY_HEAVY,
    "Becoming a Supple Leopard": SourceProfile.THEORY_HEAVY,
    "Tapering Strategies to Enhance Maximal Strength": SourceProfile.RESEARCH,
    "Short-term training cessation": SourceProfile.RESEARCH,
    "Higher vs lower intensity strength training taper": SourceProfile.RESEARCH,
    "Tapering and Peaking in the Weight Lifting Sports": SourceProfile.RESEARCH,
    "Step and Exponential Taper": SourceProfile.RESEARCH,
    "Alterations in Weightlifters across Five Months": SourceProfile.RESEARCH,
    "Block Periodized Training Cycle in Weightlifters": SourceProfile.RESEARCH,
    "How Do Master Weightlifters Train": SourceProfile.RESEARCH,
    "Weightlifting Overhead Pressing Derivatives": SourceProfile.RESEARCH,
    "Priming with Light vs. Heavy Loads": SourceProfile.RESEARCH,
    "Performance Development From Youth to Senior": SourceProfile.RESEARCH,
    "Weightlifting Pulling Derivatives": SourceProfile.RESEARCH,
    "Training for Muscular Strength": SourceProfile.RESEARCH,
    "Periodization and Block Periodization in Sports": SourceProfile.RESEARCH,
    "The training process: Planning for strength": SourceProfile.RESEARCH,
    # AUD-5 open-access additions (2026-09-22)
    "Integrating Deloading into Strength and Physique Sports": SourceProfile.RESEARCH,
    "Deloading Practices in Strength and Physique Sports": SourceProfile.RESEARCH,
    "Gaining more from doing less": SourceProfile.RESEARCH,
    "The Sleep and Recovery Practices of Athletes": SourceProfile.RESEARCH,
    "Upper Extremity Weightlifting Injury Surveillance": SourceProfile.RESEARCH,
    "Health challenges and acute sports injuries restrict weightlifting": SourceProfile.RESEARCH,
    "Repetitions in Reserve-Based Rating of Perceived Exertion": SourceProfile.RESEARCH,
    "Injuries in weightlifting and powerlifting": SourceProfile.RESEARCH,
    "menstrual cycle phase on acute strength performance": SourceProfile.RESEARCH,
    "Compatibility of Concurrent Aerobic and Strength Training": SourceProfile.RESEARCH,
    "Position Stand: protein and exercise": SourceProfile.RESEARCH,
    "position stand: caffeine and exercise performance": SourceProfile.RESEARCH,
}
