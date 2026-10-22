"""Domain enums shared by models, services and API schemas."""

from enum import StrEnum


class UserRole(StrEnum):
    SUPER_ADMIN = "super_admin"
    TRAINER = "trainer"
    COACH = "coach"
    PLAYER = "player"


class UserStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    UNSPECIFIED = "unspecified"  # self-registered adult profiles carry no gender


class SkillLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class LinkSource(StrEnum):
    SHARE_LINK = "share_link"
    EVENT_REGISTRATION = "event_registration"
    COACH_INVITE = "coach_invite"
    CAMP_CONVERSION = "camp_conversion"
    ADMIN = "admin"


class LinkStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ShareLinkKind(StrEnum):
    PLAYER_STATIC = "player_static"
    COACH_UNIQUE = "coach_unique"


class ShareLinkEventKind(StrEnum):
    OPENED = "opened"
    REGISTERED = "registered"


class ApprovalSubjectType(StrEnum):
    EVENT_RSVP = "event_rsvp"
    CONTENT = "content"
    TOKEN_PURCHASE = "token_purchase"  # noqa: S105


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class OneTimeTokenKind(StrEnum):
    EMAIL_VERIFY = "email_verify"
    PASSWORD_RESET = "password_reset"  # noqa: S105
    TRAINER_SETUP = "trainer_setup"
    EMAIL_CHANGE = "email_change"
