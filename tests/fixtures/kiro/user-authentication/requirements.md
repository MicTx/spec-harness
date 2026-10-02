# Requirements

## Purpose

Provide secure user authentication for the application: users register with email and password, log in securely, and recover from invalid or duplicate submissions with clear errors.

## Requirements

### Requirement: User Registration

The system SHALL create accounts from valid registration data and reject invalid or duplicate submissions with actionable errors.

#### Scenario: Valid registration

- **WHEN** a user submits valid registration data
- **THEN** the system shall create a new user account

#### Scenario: Duplicate email

- **WHEN** a user submits an email that already exists
- **THEN** the system shall display "Email already registered" error

#### Scenario: Invalid email format

- **WHEN** a user submits invalid email format
- **THEN** the system shall display email validation error

### Requirement: Login

The system SHALL authenticate registered users with email and password and reject wrong credentials without leaking account state.

#### Scenario: Successful login

- **WHEN** a user submits correct credentials
- **THEN** the system shall start an authenticated session

#### Scenario: Wrong password

- **WHEN** a user submits a wrong password
- **THEN** the system shall display an authentication error
