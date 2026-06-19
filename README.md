# UFC Fight Prediction Project

## Overview

This project is a Python-based data pipeline designed to collect, store, and analyze UFC fighter statistics for future predictive modeling.

The system automatically scrapes fighter information from the UFC athlete roster, extracts fighter bio information and performance statistics from individual athlete profile pages, and stores the cleaned data in a PostgreSQL database.

The long-term goal of this project is to build machine learning and statistical models capable of predicting future fight outcomes and performance metrics such as:

* Fight winner
* Significant strikes landed
* Takedowns landed
* Average fight time
* Method of victory
* Other UFC performance statistics

---

## Features

### Dynamic UFC Roster Scraping

The scraper automatically:

* Navigates the UFC athlete directory
* Detects all available roster pages
* Collects athlete profile URLs
* Detects roster updates when new pages or fighters are added

---

### Fighter Bio Extraction

The scraper extracts fighter information including:

* Name
* Status
* Place of Birth
* Training Camp
* Fighting Style
* Age
* Height
* Weight
* Reach
* Leg Reach
* UFC Octagon Debut Date

---

### Fighter Performance Statistics

The scraper extracts performance metrics including:

#### Striking Statistics

* Significant Strikes Landed
* Significant Strikes Attempted
* Significant Strike Accuracy
* Significant Strikes Landed per Minute
* Significant Strikes Absorbed per Minute
* Significant Strike Defense

#### Grappling Statistics

* Takedowns Landed
* Takedowns Attempted
* Takedown Accuracy
* Takedown Defense
* Takedown Average per 15 Minutes
* Submission Average per 15 Minutes

#### Additional Metrics

* Knockdown Average
* Average Fight Time

#### Strike Distribution

By Position:

* Standing
* Clinch
* Ground

By Target:

* Head
* Body
* Leg

#### Win Method Distribution

* KO/TKO
* Decision
* Submission

---

## Database Design

The project uses PostgreSQL.

### fighters

Stores fighter biographical information.

Example fields:

* fighter_id
* name
* status
* age
* height
* weight
* reach
* leg_reach
* fighting_style
* place_of_birth
* trains_at
* octagon_debut

### fighter_performance_stats

Stores fighter performance metrics.

Example fields:

* sig_strikes_landed
* sig_strikes_attempted
* takedowns_landed
* takedowns_attempted
* sig_strikes_landed_per_min
* sig_strikes_absorbed_per_min
* takedown_avg_per_15_min
* submission_avg_per_15_min
* sig_strikes_defense
* takedown_defense
* knockdown_avg
* average_fight_time

and additional positional, target, and win-method statistics.

---

## Technologies Used

### Programming

* Python

### Libraries

* requests
* BeautifulSoup4
* psycopg

### Database

* PostgreSQL

### Development Environment

* VS Code
* macOS

---

## Data Collection Pipeline

1. Access UFC athlete roster pages.
2. Collect athlete profile URLs.
3. Visit each athlete profile page.
4. Extract bio information.
5. Extract performance statistics.
6. Clean and normalize values.
7. Insert or update fighter records in PostgreSQL.
8. Store performance statistics linked through fighter_id.

---

## Current Dataset

Current database contains:

* 1,300+ UFC fighters
* 1,000+ fighters with detailed performance statistics
* Multiple fighter attributes and performance metrics suitable for statistical analysis and predictive modeling

---

## Future Development

Planned improvements include:

* Automated database updates
* Fight history scraping
* Event and matchup scraping
* Feature engineering
* Machine learning models
* Win probability predictions
* Significant strike predictions
* Takedown predictions
* Fight duration predictions
* Interactive dashboards and visualizations

---

## Author

Kariappa Erappa

Computer Science Graduate

Project Focus:
Sports Analytics, Data Engineering, Machine Learning, Web Scraping, and Database Systems.
