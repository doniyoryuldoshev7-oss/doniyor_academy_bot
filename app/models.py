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
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)

class AnswerLog(Base):
    __tablename__="answer_logs"
    id: Mapped[int]=mapped_column(primary_key=True)
    attempt_id: Mapped[int]=mapped_column(ForeignKey("test_attempts.id",ondelete="CASCADE"))
    question_id: Mapped[int]=mapped_column(ForeignKey("questions.id",ondelete="CASCADE"))
    selected_option: Mapped[str]=mapped_column(String(1))
    is_correct: Mapped[bool]=mapped_column(Boolean)
    question: Mapped["Question"] = relationship()

