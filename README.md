# ⚡ Daily Submission Automation - Tracker

### Year-Wise Competitive Programming Tracking, Mentoring & Analytics Platform

DSA Tracker is a web-based platform designed to help students practice Data Structures and Algorithms while enabling mentors and administrators to manage students, assign problems, track competitive-programming progress, manage contests, synchronize coding-platform submissions, and share useful recommendations.

The platform integrates **PostgreSQL, Google Sheets, Codeforces, LeetCode, and AtCoder** into a centralized system with **year-wise student isolation**.

---

## 🚀 Key Highlights

- 🎓 Year-wise student management
- 📊 Individual Google Sheet for every academic year
- 👨‍🏫 Mentor-based problem assignment and progress tracking
- 👑 Admin dashboard and system management
- 💻 Codeforces integration
- 🧩 LeetCode synchronization and import
- 🏆 AtCoder integration
- 📝 Contest management
- 💡 Year-wise recommendations and news
- 📈 Student progress analytics
- 🔄 Automatic/incremental synchronization
- 📋 Daily Tracker
- 💾 Sheet backup and restore
- 🔐 Role-based access control
- 🛡️ Student year-wise data isolation

---

# 🎯 Problem Statement

Managing DSA practice across multiple students can become difficult when student progress, coding-platform submissions, assignments, contests, and learning resources are maintained separately.

A common Google Sheet also creates several problems:

- Students from different academic years can access the same data.
- Mentors have difficulty managing different batches.
- Student progress is difficult to track centrally.
- Coding-platform submissions require manual updates.
- Assignments and recommendations are difficult to organize.
- Year-specific information becomes mixed together.

DSA Tracker solves these problems through a centralized web application with **year-wise data and Google Sheet separation**.

---

# 💡 Proposed Solution

DSA Tracker introduces a **year-wise architecture** where each academic year has its own Google Spreadsheet.

For example:

2028 Students
     │
     └── 2028 Google Sheet

2029 Students
     │
     └── 2029 Google Sheet

There is no common student Google Sheet.

Each student's academic year is stored in the database and is used to determine which Google Sheet they can access.

🏗️ System Architecture
                    ┌─────────────────────┐
                    │      Frontend       │
                    │ HTML / CSS / JS     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Flask Backend     │
                    │   Routes / APIs     │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
       ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
       │ PostgreSQL  │  │ Sync Engine │  │  Services   │
       │  Database   │  │             │  │             │
       └─────────────┘  └──────┬──────┘  └──────┬──────┘
                               │                │
                  ┌────────────┼────────────────┤
                  │            │                │
                  ▼            ▼                ▼
             Codeforces    LeetCode          AtCoder
                              
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Year Sheet Service  │
                    └──────────┬──────────┘
                               │
                 ┌─────────────┴─────────────┐
                 ▼                           ▼
          2028 Google Sheet          2029 Google Sheet
🎓 Year-Wise Architecture

The main architectural feature of the project is year-wise isolation.

Each student has a cohort_year.

Example:

Student A
cohort_year = 2028
        ↓
2028 Google Sheet
Student B
cohort_year = 2029
        ↓
2029 Google Sheet

The database maintains the relationship:

Year → Google Spreadsheet

through the year_sheets table.

The centralized:

services/year_sheet_service.py

handles year-to-spreadsheet resolution.

Student Access

Students can access only their own year's data.

2028 Student
      ↓
2028 Data
      ↓
2028 Google Sheet

A 2028 student cannot switch to the 2029 sheet through a client-side year parameter.

Mentor/Admin Access

Mentors and administrators can select a configured year.

Mentor
  ↓
Select 2028
  ↓
2028 Students
  ↓
2028 Google Sheet
Mentor
  ↓
Select 2029
  ↓
2029 Students
  ↓
2029 Google Sheet
👥 User Roles
👑 Admin

The administrator manages the overall platform.

Admin Features
Admin authentication
User monitoring
Student management
Year/cohort configuration
Google Sheet configuration
Year → Spreadsheet mapping
Synchronization
Backup and restore
Daily Tracker management
System monitoring
👨‍🏫 Mentor

Mentors manage students and monitor their DSA progress.

Mentor Features
Year selection
View students by academic year
Assign DSA problems
Search problems
Create custom problems
Track assignment progress
Manage contests
Manage recommendations
View year-specific student progress
Access year-specific Google Sheets
👨‍🎓 Student

Students use the platform to track their competitive-programming journey.

Student Features
Year-wise signup
Student dashboard
Coding-platform profiles
Codeforces tracking
LeetCode tracking
AtCoder tracking
Submission synchronization
Problem tracking
Mentor assignments
Contest participation
Progress monitoring
Recommendations and news
Personal year-specific Google Sheet
Daily Tracker
Reports and analytics
💻 Coding Platform Integration
Codeforces

The platform can synchronize Codeforces activity and submissions.

Codeforces
     ↓
Sync Engine
     ↓
DSA Tracker
     ↓
PostgreSQL + Year Sheet
LeetCode

The project supports LeetCode data synchronization and import functionality.

The implementation supports authenticated/session-based workflows where required and maintains synchronized problem/submission information.

LeetCode
    ↓
Import / Sync
    ↓
DSA Tracker
    ↓
Student Progress
AtCoder

AtCoder problem and submission-related data can be integrated into the tracker.

📊 Student Progress Tracking

The platform provides students and mentors with progress information such as:

Problems solved
Platform-wise activity
Assignment status
Synchronization status
Contest activity
Recent progress
Coding-platform information

Mentors can use this information to monitor students within a selected academic year.

📌 Problem Assignment

Mentors can assign problems to students.

Mentor
   ↓
Select Year
   ↓
Select Student
   ↓
Select/Create Problem
   ↓
Assign Problem
   ↓
Student Dashboard

Assignments can include information such as:

Problem name
Platform
Difficulty
Topic
Deadline
Progress
Status
🏆 Contest Management

The platform provides contest-related functionality for managing competitive-programming contests.

The system includes contest-related entities such as:

Contest events
Contest problems
Contest results
Contest synchronization
Leaderboards

Contest information can be managed according to the supported year-wise architecture.

💡 Recommendations & News

A dedicated recommendation system allows mentors to share useful information with students.

Examples include:

Internship opportunities
Hackathons
Coding contests
Learning resources
Important announcements
Competitive-programming resources
Career opportunities

Recommendations are organized according to the appropriate student year.

Mentor
   ↓
Recommendation
   ↓
Academic Year
   ↓
Students of that Year

This prevents unrelated year groups from receiving mixed recommendations.

📋 Daily Tracker

The Daily Tracker helps monitor student DSA activity and maintains tracking information through the application's database and Google Sheet integration.

📊 Google Sheets Integration

Google Sheets is used as an external tracking layer for student DSA activity.

Each academic year has an independent spreadsheet.

Example:

Academic Year	Google Sheet
2028	2028 Student Sheet
2029	2029 Student Sheet
2030	2030 Student Sheet

The application uses the centralized year-sheet resolver to determine the correct spreadsheet.

Student / Mentor / Admin
          ↓
      Selected Year
          ↓
   year_sheet_service
          ↓
       year_sheets
          ↓
   Google Spreadsheet

Spreadsheet IDs and URLs are normalized before accessing Google Sheets.

🗄️ Database

The project uses PostgreSQL as its primary database.

Important data areas include:

Users
Student cohort/year information
Submissions
Mentor assignments
Recommendations
Year-wise sheet mappings
Contest data
Daily tracker data
Notifications
Sessions
Logs
Cache information
Reports

Important year-wise fields include:

users.cohort_year

and:

year_sheets
🛠️ Technology Stack
Frontend
HTML5
CSS3
JavaScript
Backend
Python
Flask
Database
PostgreSQL
psycopg2
Google Integration
Google Sheets API
gspread
Google Service Account
Coding Platforms
Codeforces
LeetCode
AtCoder
Development
Git
GitHub
VS Code
📁 Project Structure
DSA_TRACKER/
│
├── backend/
│   │
│   ├── database/
│   │   ├── db.py
│   │   ├── migrate.py
│   │   ├── seed.py
│   │   ├── indexes.sql
│   │   └── migrations/
│   │
│   ├── services/
│   │   ├── year_sheet_service.py
│   │   ├── auth_service.py
│   │   ├── mentor_sheet_sync.py
│   │   └── ...
│   │
│   ├── routes/
│   │   ├── admin.py
│   │   ├── google_sheet.py
│   │   └── ...
│   │
│   ├── contest/
│   │
│   ├── app.py
│   ├── normal_sync.py
│   ├── bot_sheet_sync.py
│   └── requirements.txt
│
├── frontend/
│   ├── assets/
│   │   ├── css/
│   │   └── js/
│   │
│   ├── admin.html
│   ├── mentor.html
│   ├── dashboard.html
│   ├── signup.html
│   └── ...
│
├── README.md
└── .gitignore

Project structure may evolve as new modules and features are added.

🔄 Application Workflow
Student Workflow
Signup
   ↓
Select Academic Year
   ↓
Account Creation
   ↓
Login
   ↓
Student Dashboard
   ↓
Coding Platform Sync
   ↓
Problem Tracking
   ↓
Mentor Assignments
   ↓
Progress Tracking
   ↓
Recommendations
Mentor Workflow
Mentor Login
      ↓
Select Academic Year
      ↓
View Students
      ↓
Assign Problems
      ↓
Track Progress
      ↓
Manage Contests
      ↓
Share Recommendations
      ↓
Manage Year-Specific Data
Admin Workflow
Admin Login
      ↓
Admin Dashboard
      ↓
Configure Academic Years
      ↓
Map Year → Google Spreadsheet
      ↓
Monitor Students
      ↓
Synchronization
      ↓
Backup / Restore
🔐 Security & Access Control

The platform implements year-aware access control.

Student

A student's academic year is taken from the authenticated user record.

current_user.cohort_year

The client cannot simply change the year parameter to access another cohort's data.

Mentor/Admin

Mentors and administrators can work with configured years through the year-selection system.

Secrets

Sensitive credentials are stored using environment variables and should never be committed to GitHub.

Examples:

DATABASE_URL
ADMIN_INIT_PASSWORD
Google service-account credentials
API credentials
Session secrets
⚙️ Installation
1. Clone the Repository
git clone <your-github-repository-url>
cd DSA_TRACKER
2. Create Virtual Environment
Windows
python -m venv venv
venv\Scripts\activate
Linux / macOS
python3 -m venv venv
source venv/bin/activate
3. Install Backend Dependencies
cd backend
pip install -r requirements.txt
🗄️ Database Configuration

The project requires PostgreSQL.

Set the PostgreSQL connection string using:

DATABASE_URL=your_postgresql_connection_string

Never commit the actual connection string.

🔑 Environment Variables

Create a .env file based on the variables used by the project.

Example:

DATABASE_URL=
ADMIN_INIT_PASSWORD=

Add any other required environment variables used by your deployment configuration.

⚠️ Never commit
.env
service-account JSON
private keys
API keys
passwords
tokens
🗃️ Database Migration

After configuring PostgreSQL:

python database/migrate.py

The migration system applies the project's database schema and migrations.

For a fresh environment, the seed script can be used when required:

python database/seed.py
📊 Google Sheets Setup

For each academic year:

Step 1

Create a separate Google Spreadsheet.

Example:

DSA Tracker — 2028
DSA Tracker — 2029
Step 2

Share each spreadsheet with the Google service account used by the backend.

Step 3

Configure the year-to-spreadsheet mapping through the application's year management interface.

Example:

2028 → 2028 Spreadsheet
2029 → 2029 Spreadsheet
Step 4

Verify the selected year can access the correct spreadsheet.

▶️ Running the Application

Start the backend using the project's configured Flask startup command.

Example:

python app.py

The frontend can then be served according to the project's frontend/deployment configuration.

🧪 Testing

Before deployment, test the following:

Year Isolation
2028 Student
    ↓
2028 Sheet ✅

2029 Student
    ↓
2029 Sheet ✅
Cross-Year Protection
2028 Student
    ↓
Attempt to access 2029
    ↓
Access denied / ignored ✅
Mentor
Mentor → 2028 → 2028 students ✅
Mentor → 2029 → 2029 students ✅
Google Sheets
2028 → 2028 Spreadsheet ✅
2029 → 2029 Spreadsheet ✅
🌐 Deployment Architecture

A typical deployment consists of:

                    Users
                      │
                      ▼
               ┌─────────────┐
               │  Frontend   │
               └──────┬──────┘
                      │
                      ▼
               ┌─────────────┐
               │   Flask     │
               │   Backend   │
               └──────┬──────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
      PostgreSQL   Google      Coding
                   Sheets      Platforms

Production credentials must be configured through the deployment platform's environment-variable settings.

📸 Screenshots

Add project screenshots here.

Admin Dashboard
docs/screenshots/admin-dashboard.png
Mentor Dashboard
docs/screenshots/mentor-dashboard.png
Student Dashboard
docs/screenshots/student-dashboard.png
Recommendation Page
docs/screenshots/recommendations.png
🚀 Future Enhancements

Potential future improvements include:

🤖 AI-based problem recommendations
📈 Advanced student analytics
📊 More detailed mentor reports
🔔 Improved notification system
📱 Mobile application
🧠 Personalized DSA learning paths
🏆 Advanced contest analytics
📑 Automated performance reports
👨‍💻 Project Goals

The main goals of DSA Tracker are to:

Simplify DSA progress tracking
Reduce manual Google Sheet management
Separate students by academic year
Help mentors manage multiple student cohorts
Automate coding-platform synchronization
Provide structured problem assignments
Improve competitive-programming consistency
Provide useful learning and career recommendations
📌 Core Architecture Principle

The most important principle of the platform is:

One academic year → One isolated student cohort → One dedicated Google Spreadsheet

For example:

2028
 ├── 2028 Students
 ├── 2028 Assignments
 ├── 2028 Recommendations
 └── 2028 Google Sheet

2029
 ├── 2029 Students
 ├── 2029 Assignments
 ├── 2029 Recommendations
 └── 2029 Google Sheet

This architecture prevents different student cohorts from mixing their data.

🤝 Contributing

Contributions are welcome.

git checkout -b feature/your-feature
git add .
git commit -m "Add your feature"
git push origin feature/your-feature

Create a pull request with a clear description of the changes.

📄 License

This project is currently maintained as an academic/project development system.

Add the appropriate license here if the project is intended to be open source.

⭐ DSA Tracker

A centralized platform for DSA practice, competitive programming, mentoring, progress tracking, contests, recommendations, and year-wise student management
