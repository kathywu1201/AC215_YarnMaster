from fastapi import FastAPI
from pydantic import BaseModel
from unittest.mock import patch
import requests
import numpy as np
from fastapi.responses import FileResponse
import os

# Mock function for image_to_vector
def mock_image_to_vector(image_path):
    # Return a 1024-dimensional numpy vector
    return np.random.rand(1024)

# Mock function for query
def mock_query(user_query, image_vector):
    # Simulate chunked text based on user_query and image_vector
    combined_text_chunks = "Mock chunked text generated by RAG."
    return {"prompt": f"{user_query} {combined_text_chunks}"}


app = FastAPI()

# Input model for the /vector_generator endpoint
class VectorInput(BaseModel):
    image_path: str

@app.post("/vector_generator")
async def vector_generator(input_data: VectorInput):
    vector = mock_image_to_vector(input_data.image_path) # Replace with real function in production
    vector_file_path = "vector.npy"
    np.save(vector_file_path, vector)
    return FileResponse(vector_file_path, media_type="application/octet-stream")


# Input model for the /rag endpoint
class RAGInput(BaseModel):
    user_query: str
    image_vector: str #Path to the vector file
    
app.post("/rag")
async def rag_query(input_data: RAGInput):
    image_vector_np = np.load(input_data.image_vector)
    output = mock_query(input_data.user_query, image_vector_np)  # Replace with real function in production
    return output


# Integration test
@patch("app.mock_image_to_vector", side_effect=mock_image_to_vector)
@patch("app.mock_query", side_effect=mock_query)
def test_integration_vector_rag(mock_image_to_vector_func, mock_query_func):
    # Step 1: Test /vector_generator
    vector_response = requests.post(
        "http://localhost:8000/vector_generator",
        json={"image_path": "mock_image_path.png"}
    )
    assert vector_response.status_code == 200, "Failed to get vector"

    # Save the downloaded vector file locally
    with open("test_vector.npy", "wb") as f:
        f.write(vector_response.content)
    
    # Load the vector to verify it is a NumPy array
    vector = np.load("test_vector.npy")
    assert isinstance(vector, np.ndarray), "Vector is not a NumPy array"
    assert vector.shape == (1024,), f"Vector shape is incorrect, got {vector.shape}"

    # Step 2: Test /rag
    rag_response = requests.post(
        "http://localhost:8000/rag",
        json={
            "user_query": "Describe the crochet pattern in the following image.",
            "image_vector": "test_vector.npy"
        }
    )
    assert rag_response.status_code == 200, "Failed to get RAG query response"
    rag_result = rag_response.json()

    # Assert output is user_query + chunked_text
    expected_output = {"prompt": "Describe the crochet pattern in the following image. Mock chunked text generated by RAG."}
    assert rag_result == expected_output, f"Unexpected output: {rag_result}"
    print(f"Integration Test Passed. Output: {rag_result}")

    # Clean up temporary files
    os.remove("test_vector.npy")