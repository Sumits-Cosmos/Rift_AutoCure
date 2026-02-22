import dotenv from "dotenv";
dotenv.config();

export const env = {
  PORT: process.env.PORT || 5000,
  FASTAPI_URL: process.env.FASTAPI_URL || process.env.FastAPI_URL || "http://localhost:8000",
  STORAGE_DIR: process.env.STORAGE_DIR || "./storage"
};