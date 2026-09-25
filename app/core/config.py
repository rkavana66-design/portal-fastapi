from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days
    client_url: str = "http://localhost:5173"

    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_pass: str
    email_from: str
    resend_api_key: str = ""

    trusted_issuer_domains: str = "credly.com,cisco.com,nptel.ac.in,coursera.org,aicte-india.org"
    upload_dir: str = "uploads"

    class Config:
        env_file = ".env"


settings = Settings()
