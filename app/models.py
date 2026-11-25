from datetime import datetime
from sqlalchemy import String, Boolean, ForeignKey, DateTime, Integer, Text, BigInteger, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

class User(Base):
    __tablename__="users"
    id: Mapped[int]=mapped_column(primary_key=True)
    telegram_id: Mapped[int]=mapped_column(BigInteger, unique=True,index=True)
    username: Mapped[str|None]=mapped_column(String(255))
    full_name: Mapped[str]=mapped_column(String(255))
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    total_tests: Mapped[int]=mapped_column(Integer,default=0)
    total_questions: Mapped[int]=mapped_column(Integer,default=0)
    correct_answers: Mapped[int]=mapped_column(Integer,default=0)
    total_score: Mapped[int]=mapped_column(Integer,default=0)
    is_blocked: Mapped[bool]=mapped_column(Boolean,default=False)
    phone_number: Mapped[str | None]=mapped_column(String(30))
    group_name: Mapped[str | None]=mapped_column(String(100))
    registration_status: Mapped[str]=mapped_column(String(20),default="pending")

class Subject(Base):
    __tablename__="subjects"
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(255),unique=True)
    description: Mapped[str|None]=mapped_column(Text)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True)
    topics: Mapped[list["Topic"]]=relationship(back_populates="subject",cascade="all, delete-orphan")

class Topic(Base):
    __tablename__="topics"
    id: Mapped[int]=mapped_column(primary_key=True)
    subject_id: Mapped[int]=mapped_column(ForeignKey("subjects.id",ondelete="CASCADE"))
    name: Mapped[str]=mapped_column(String(255))
    subject: Mapped["Subject"]=relationship(back_populates="topics")
    questions: Mapped[list["Question"]]=relationship(back_populates="topic",cascade="all, delete-orphan")

class Question(Base):
    __tablename__ = "questions"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=__import__("sqlalchemy").true())

    id: Mapped[int] = mapped_column(primary_key=True)

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE")
    )

    text: Mapped[str | None] = mapped_column(Text, nullable=True)

    image_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )


    question_mode: Mapped[str] = mapped_column(
        String(20),
        default="closed"
    )

    option_a: Mapped[str | None] = mapped_column(Text, nullable=True)
    option_b: Mapped[str | None] = mapped_column(Text, nullable=True)
    option_c: Mapped[str | None] = mapped_column(Text, nullable=True)
    option_d: Mapped[str | None] = mapped_column(Text, nullable=True)

    correct_option: Mapped[str | None] = mapped_column(
        String(1),
        nullable=True
    )

    correct_answer: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    explanation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    topic: Mapped["Topic"] = relationship(
        back_populates="questions"
    )


class GroupQuiz(Base):
    __tablename__ = "group_quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    creator_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE")
    )
    mode: Mapped[str] = mapped_column(
        String(20),
        default="question"
    )
    question_ids_json: Mapped[str] = mapped_column(Text)
    poll_map_json: Mapped[str] = mapped_column(
        Text,
        default="{}"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="active"
    )
    total_questions: Mapped[int] = mapped_column(
        Integer,
        default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )


class GroupQuizParticipant(Base):
    __tablename__ = "group_quiz_participants"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_quiz_id: Mapped[int] = mapped_column(
        ForeignKey("group_quizzes.id", ondelete="CASCADE"),
        index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )
    score: Mapped[int] = mapped_column(
        Integer,
        default=0
    )
    answered: Mapped[int] = mapped_column(
        Integer,
        default=0
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )
    attempt_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )


class GroupQuizAnswer(Base):
    __tablename__ = "group_quiz_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_quiz_id: Mapped[int] = mapped_column(
        ForeignKey("group_quizzes.id", ondelete="CASCADE"),
        index=True
    )
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("group_quiz_participants.id", ondelete="CASCADE"),
        index=True
    )
    question_id: Mapped[int] = mapped_column(Integer)
    selected_option: Mapped[str] = mapped_column(
        String(20)
    )
    is_correct: Mapped[bool] = mapped_column(
        Boolean,
        default=False
    )
    answered_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )

class TestAttempt(Base):
    __tablename__="test_attempts"
    id: Mapped[int]=mapped_column(primary_key=True)
    user_id: Mapped[int]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"))
    topic_id: Mapped[int]=mapped_column(ForeignKey("topics.id",ondelete="CASCADE"))
    total: Mapped[int]=mapped_column(Integer)
    correct: Mapped[int]=mapped_column(Integer)
    score: Mapped[int]=mapped_column(Integer)
    source: Mapped[str]=mapped_column(String(20),default="quiz")
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)

class AnswerLog(Base):
    __tablename__="answer_logs"
    id: Mapped[int]=mapped_column(primary_key=True)
    attempt_id: Mapped[int]=mapped_column(ForeignKey("test_attempts.id",ondelete="CASCADE"))
    question_id: Mapped[int]=mapped_column(ForeignKey("questions.id",ondelete="CASCADE"))
    selected_option: Mapped[str]=mapped_column(String(1))
    is_correct: Mapped[bool]=mapped_column(Boolean)
    resolved: Mapped[bool]=mapped_column(Boolean,default=False)
    question: Mapped["Question"] = relationship()


# Smart Takrorlash uchun alohida jadval
from sqlalchemy import UniqueConstraint as _UniqueConstraint


class SmartReview(Base):
    __tablename__ = "smart_reviews"

    __table_args__ = (
        _UniqueConstraint(
            "user_id", "question_id",
            name="uq_smart_review_user_question"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )

    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"),
        index=True
    )

    stage: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False
    )

    error_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    correct_streak: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )


class QuestionEditorGrant(Base):
    __tablename__ = "question_editor_grants"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )

    granted_by: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    granted_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


class AdminRoleEvent(Base):
    __tablename__ = "admin_role_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    actor_telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    target_telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    action: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


# Ustoz topshiriqlari
class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"),
        index=True,
    )

    created_by: Mapped[int] = mapped_column(BigInteger, index=True)

    # Kimga: aniq o'quvchi -> user_id, guruh -> group_name,
    # ikkalasi ham bo'sh bo'lsa, hammaga.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    group_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    required_questions: Mapped[int] = mapped_column(Integer, default=10)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    deadline: Mapped[datetime] = mapped_column(DateTime, index=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )


# O'qituvchi (ustoz) tizimi
class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )


class TeacherGroup(Base):
    __tablename__ = "teacher_groups"
    __table_args__ = (
        _UniqueConstraint("teacher_id", "group_name", name="uq_teacher_group"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id", ondelete="CASCADE"),
        index=True,
    )

    group_name: Mapped[str] = mapped_column(String(100))


# Imtihon rejimi
class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    subject_name: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(10), default="milliy")
    created_by: Mapped[int] = mapped_column(BigInteger, index=True)
    group_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duration_min: Mapped[int] = mapped_column(Integer, default=60)
    closes_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    is_removed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExamItem(Base):
    __tablename__ = "exam_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"),
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(Text)


class ExamAttempt(Base):
    __tablename__ = "exam_attempts"
    __table_args__ = (
        _UniqueConstraint("exam_id", "user_id", name="uq_exam_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deadline_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active", index=True)
    answers: Mapped[str] = mapped_column(Text, default="{}")


class OpenQuestion(Base):
    __tablename__ = "open_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"),
        index=True,
    )
    stem: Mapped[str] = mapped_column(Text)
    parts: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
