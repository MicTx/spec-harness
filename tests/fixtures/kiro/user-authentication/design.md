# Design

## Architecture

The authentication module consists of a registration service, a validation library, and a user repository. The registration service orchestrates validation before persistence; the repository enforces the unique email constraint at the storage layer.

## Sequence Diagram

```mermaid
sequenceDiagram
    participant U as User
    participant API as Registration API
    participant DB as User Repository
    U->>API: POST /register (email, password)
    API->>API: validate email format
    API->>DB: create user
    DB-->>API: conflict on duplicate email
    API-->>U: 201 Created / 409 Conflict
```

## Testing Strategy

- Unit tests for email format and password validation rules
- Integration tests for the registration endpoint, including duplicate email conflicts
