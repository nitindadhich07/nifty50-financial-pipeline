from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
import datetime
import os

Base = declarative_base()

class Company(Base):
    __tablename__ = 'companies'
    id = Column(Integer, primary_key=True)
    symbol = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    sector = Column(String)
    filings = relationship("Filing", back_populates="company")

class Filing(Base):
    __tablename__ = 'filings'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey('companies.id'))
    announcement_id = Column(String, unique=True) # For de-duplication
    announcement_date = Column(DateTime)
    title = Column(String)
    source = Column(String) # 'NSE', 'BSE', 'InvestorRelations'
    pdf_path = Column(String)
    pdf_hash = Column(String) # For de-duplication
    category = Column(String)
    downloaded_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    company = relationship("Company", back_populates="filings")

# Database initialization
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'financial_pipeline.db')
ENGINE_URL = f'sqlite:///{DB_PATH}'

engine = create_engine(ENGINE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)
