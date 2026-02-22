import os
from dotenv import load_dotenv

print(f"Before loading: USE_DOCKER={os.getenv('USE_DOCKER')}")
load_dotenv()
print(f"After loading: USE_DOCKER={os.getenv('USE_DOCKER')}")
