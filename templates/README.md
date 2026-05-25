# Template Organization

This directory contains all templates for the Vietnam Hearts application, organized by purpose and type.

## Directory Structure

```
templates/
├── web/                    # Web page templates (Jinja2)
│   ├── base.html          # Base template with common layout
│   ├── home.html          # Home page
│   ├── admin/             # Admin interface templates
│   │   └── dashboard.html
│   ├── auth/              # Authentication templates
│   │   ├── access_denied.html
│   │   ├── access_verification_failed.html
│   │   ├── auth_failed.html
│   │   ├── oauth_error.html
│   │   └── signin_success.html
│   └── unsubscribe/       # Email preference management
│       ├── already_unsubscribed.html
│       ├── error.html
│       └── manage_preferences.html
├── email/                  # Email-specific templates
│   ├── base_email.html    # Base email template
│   ├── confirmation-email.html
│   ├── weekly-reminder-email.html
│   └── components/        # Reusable email components
└── components/             # Reusable web components
    └── (future use)
```

## Template Types

### Web Templates (`web/`)
- **Purpose**: HTML pages served to web browsers
- **Engine**: Jinja2 (FastAPI templating)
- **Features**: 
  - Bootstrap CSS framework
  - Responsive design
  - JavaScript functionality
  - SEO optimization
- **Base Template**: `base.html` provides common layout, navigation, and styling

### Email Templates (`email/`)
- **Purpose**: HTML emails sent to volunteers
- **Features**:
  - Email-safe CSS (inline styles)
  - Maximum email client compatibility
  - Responsive design for mobile
  - Consistent branding
- **Base Template**: `base_email.html` provides common email structure and styling

## Usage

### Web Templates
```python
# In FastAPI routes
templates = Jinja2Templates(directory="templates/web")
return templates.TemplateResponse("home.html", {"request": request, "version": version})
```

### Email Templates
```python
# In email service
with open("templates/email/confirmation-email.html", "r") as f:
    template_content = f.read()
```

## Best Practices

1. **Always extend base templates** for consistency
2. **Use template inheritance** to avoid code duplication
3. **Keep email templates simple** for maximum compatibility
4. **Test templates** in different email clients and browsers
5. **Use semantic HTML** for accessibility
6. **Optimize images** for web and email delivery

## Template Variables

### Common Variables
- `version`: Application version
- `current_user`: Current authenticated user (if any)
- `request`: FastAPI request object

### Email-Specific Variables
- `UserFullName`: Volunteer's full name
- `INVITE_LINK_ZALO`: Zalo group chat invite link
- `SCHEDULE_SIGNUP_LINK`: Volunteer schedule link
- `EMAIL_PREFERENCES_LINK`: Email preferences management link

## Maintenance

- **Web templates**: Update for new features, design changes, or bug fixes
- **Email templates**: Test in multiple email clients before deployment
- **Base templates**: Changes affect all extending templates
- **Components**: Reusable across multiple templates

## Security Notes

- Templates are rendered server-side
- User input is automatically escaped by Jinja2
- Email templates should not contain sensitive information
- Always validate template variables before rendering
