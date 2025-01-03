import os
import argparse
import pandas as pd
import numpy as np
import json
import glob
import hashlib
import chromadb
import shutil
from google.cloud import storage

# Vertex AI
import vertexai
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

# Semantic Splitter
from semantic_splitter import SemanticChunker

# Setup
GCP_PROJECT = os.environ["GCP_PROJECT"]
GCP_LOCATION = "us-central1"
EMBEDDING_MODEL = "text-embedding-004"
EMBEDDING_DIMENSION = 256
INPUT_FOLDER = "input_datasets"
OUTPUT_FOLDER = "outputs"
JSON_OUTPUT = "json_outputs"
DATA_OUTPUT = "data_prep"
BUCKET_NAME = "crochet-patterns-bucket"
CHROMADB_HOST = os.environ["CHROMADB_HOST"]
CHROMADB_PORT = os.environ["CHROMADB_PORT"]
vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
embedding_model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL)


def download():
	print("download")

	shutil.rmtree(INPUT_FOLDER, ignore_errors=True, onerror=None)
	os.makedirs(INPUT_FOLDER, exist_ok=True)

	client = storage.Client()
	bucket = client.get_bucket(BUCKET_NAME)

	bucket_folder = "training"

	blobs = bucket.list_blobs(prefix=bucket_folder+"/")

	for blob in blobs:
		print(f"Downloading: {blob.name}")

		# Skip "directory" blobs (those that end with a "/")
		if not blob.name.endswith("/"):
			# Get the relative path by removing the bucket folder prefix
			relative_path = os.path.relpath(blob.name, bucket_folder)

			# Construct the full local path
			local_path = os.path.join(INPUT_FOLDER, relative_path)

			# Create the local directory structure if it doesn't exist
			local_dir = os.path.dirname(local_path)
			os.makedirs(local_dir, exist_ok=True)

			# Download the file to the local path
			blob.download_to_filename(local_path)

	print("Download completed.")


def generate_query_embedding(query):
	'''
	This functions takes a query and generates a text embedding using Vertex AI's embedding model.
	Input: Query string.
	Output: A list representing the query embedding.
	'''
	query_embedding_inputs = [TextEmbeddingInput(task_type='RETRIEVAL_DOCUMENT', text=query)]
	kwargs = dict(output_dimensionality=EMBEDDING_DIMENSION) if EMBEDDING_DIMENSION else {}
	embeddings = embedding_model.get_embeddings(query_embedding_inputs, **kwargs)
	return embeddings[0].values


def generate_text_embeddings(chunks, dimensionality: int = 256, batch_size=250):
	'''
	This function generates embeddings for multiple chunks of text.
	Input: A list of text chunks and optional parameters like embedding dimensionality and batch size.
	Output: A list of embeddings for the input chunks.
	'''
	# Max batch size is 250 for Vertex AI
	all_embeddings = []
	for i in range(0, len(chunks), batch_size):
		batch = chunks[i:i+batch_size]
		inputs = [TextEmbeddingInput(text, "RETRIEVAL_DOCUMENT") for text in batch]
		kwargs = dict(output_dimensionality=dimensionality) if dimensionality else {}
		embeddings = embedding_model.get_embeddings(inputs, **kwargs)
		all_embeddings.extend([embedding.values for embedding in embeddings])

	return all_embeddings


def load_text_and_image_embeddings(df, collection, batch_size=500):
	'''
	This function will load the text and image embeddings into the vector db.
	'''
	df["id"] = df.index.astype(str)
	hashed_books = df["book"].apply(lambda x: hashlib.sha256(x.encode()).hexdigest()[:16])
	df["id"] = hashed_books + "-" + df["id"]

	metadata = {
		"book": df["book"].tolist()[0]
	}

	total_inserted = 0
	for i in range(0, df.shape[0], batch_size):
		batch = df.iloc[i:i+batch_size].copy().reset_index(drop=True)
		ids = batch["id"].tolist()
		documents = batch["chunk"].tolist()
		metadatas = [metadata for _ in batch["book"].tolist()]

		# Concatenate text and image embeddings
		combined_embeddings = []
		for text_emb, image_emb in zip(batch["embedding"], batch["image_embedding"]):
			# Ensure both text and image embeddings are lists (if stored in another type, like numpy arrays)
			if isinstance(text_emb, np.ndarray):
				text_emb = text_emb.tolist()
			if isinstance(image_emb, np.ndarray):
				image_emb = image_emb.tolist()
			# print(image_emb)

			# Combine text and image embeddings
			combined_embeddings.append(text_emb + image_emb)

		# Add to the collection
		collection.add(
			ids=ids,
			documents=documents,
			metadatas=metadatas,
			embeddings=combined_embeddings  # Combined text + image embeddings
		)
		total_inserted += len(batch)
		print(f"Inserted {total_inserted} items...")

	print(f"Finished inserting {total_inserted} items into collection '{collection.name}'")



def chunk():
	os.makedirs(OUTPUT_FOLDER, exist_ok=True)
	text_files = glob.glob(os.path.join(INPUT_FOLDER, "text_instructions/txt_outputs", "*.txt"))
	print("Number of files to process:", len(text_files))

	for text_file in text_files:
		print("Processing file:", text_file)
		filename = os.path.basename(text_file)
		book_name = filename.split(".")[0]

		with open(text_file) as f:
			input_text = f.read()

		# Using semantic splitting exclusively
		text_splitter = SemanticChunker(embedding_function=generate_text_embeddings)
		text_chunks = text_splitter.create_documents([input_text])
		text_chunks = [doc.page_content for doc in text_chunks]
		print("Number of chunks:", len(text_chunks))

		data_df = pd.DataFrame(text_chunks, columns=["chunk"])
		data_df["book"] = book_name
		print("Shape:", data_df.shape)
		# print(data_df.head())

		jsonl_filename = os.path.join(OUTPUT_FOLDER, f"chunks-{book_name}.jsonl")
		with open(jsonl_filename, "w") as json_file:
			json_file.write(data_df.to_json(orient='records', lines=True))


def embed():
	jsonl_files = glob.glob(os.path.join(OUTPUT_FOLDER, f"chunks-*.jsonl"))
	print("Number of files to process:", len(jsonl_files))

	for jsonl_file in jsonl_files:
		print("Processing file:", jsonl_file)

		data_df = pd.read_json(jsonl_file, lines=True)
		print("Shape:", data_df.shape)
		# print(data_df.head())

		chunks = data_df["chunk"].values
		text_embeddings = generate_text_embeddings(chunks, EMBEDDING_DIMENSION, batch_size=100)
		data_df["embedding"] = text_embeddings

		# Load the corresponding pre-generated image embedding (.npy file)
		book_name = data_df["book"].iloc[0]  # Extract the book name
		image_embedding_file = os.path.join(INPUT_FOLDER, "image_vectors", f"{book_name}.npy")

		if os.path.exists(image_embedding_file):
			print(f"Loading image embedding from: {image_embedding_file}")
			image_embedding = np.load(image_embedding_file)  # Load the image embedding from the .npy file

			# Assuming one image embedding for the entire book, we replicate it for all chunks
			data_df["image_embedding"] = [image_embedding] * len(data_df)
		else:
			print(f"Warning: No image embedding found for {book_name}")
			data_df["image_embedding"] = [None] * len(data_df)

		jsonl_filename = jsonl_file.replace("chunks-", "embeddings-")
		with open(jsonl_filename, "w") as json_file:
			json_file.write(data_df.to_json(orient='records', lines=True))


def load():
	client = chromadb.HttpClient(host=CHROMADB_HOST, port=CHROMADB_PORT)
	collection_name = "semantic-text-image-collection"
	print("Creating collection:", collection_name)

	try:
		client.delete_collection(name=collection_name)
		print(f"Deleted existing collection '{collection_name}'")
	except Exception:
		print(f"Collection '{collection_name}' did not exist. Creating new.")

	collection = client.create_collection(name=collection_name, metadata={"hnsw:space": "cosine"}) # , metadata={"hnsw:space": "cosine"}
	print(f"Created new empty collection '{collection_name}'")

	jsonl_files = glob.glob(os.path.join(OUTPUT_FOLDER, f"embeddings-*.jsonl"))
	print("Number of files to process:", len(jsonl_files))

	for jsonl_file in jsonl_files:
		print("Processing file:", jsonl_file)

		data_df = pd.read_json(jsonl_file, lines=True)
		print("Shape:", data_df.shape)
		# print(data_df.head())

		load_text_and_image_embeddings(data_df, collection)



def query():
	client = chromadb.HttpClient(host=CHROMADB_HOST, port=CHROMADB_PORT)
	collection_name = "semantic-text-image-collection"
	collection = client.get_collection(name=collection_name)

	text_file_path = "user_inputs/ALS0537-030775M.txt"
	# User input query, if this is empty, will replace with all 0s
	try:
		with open(text_file_path, 'r') as f:
			query = f.read()
	except FileNotFoundError:
		query = "Text instruction not found."
	except Exception as e:
		print(f"An error occurred: {e}")
		query = "null"
	query_embedding = generate_query_embedding(query)

	# Since the collection expects 1280-dimensional embeddings (256 text + 1024 image),
	# we need to concatenate a 1024-dimensional dummy image embedding to the query
	dummy_image_embedding = [0.0] * 1024  # 1024-dimensional zero vector

	# Concatenate text embedding and dummy image embedding
	combined_text_query_embedding = query_embedding + dummy_image_embedding

	# Perform the text query with the combined embedding
	text_results = collection.query(
		query_embeddings=[combined_text_query_embedding], 
		n_results=10
	)
	# print("Text Query Results:", text_results)

	# Load the user input image query embedding (which is 1024-dimensional)
	image_embedding_path = "user_inputs/ALS0537-030775M.npy"
	image_query_embedding = np.load(image_embedding_path)

	# Concatenate dummy text embedding (256-dimensional zero vector) to the image query embedding
	dummy_text_embedding = [0.0] * EMBEDDING_DIMENSION  # 256-dimensional zero vector

	# Concatenate the dummy text embedding with the image query embedding
	combined_image_query_embedding = dummy_text_embedding + image_query_embedding.tolist()

		# Perform the image query with the combined embedding (1280-dimensional)
	image_results = collection.query(
		query_embeddings=[combined_image_query_embedding], 
		n_results=10
	)
	# print("Image Query Results:", image_results)

	# Re-rank the results based on both text and image queries
	ranked_results = re_rank_results(text_results, image_results, text_weight=0.6, image_weight=0.4)

	# print("Ranked Combined Results:", ranked_results)

	# Extract document IDs from ranked results
	result_ids = [result['id'] for result in ranked_results]
	print("Result IDs:", result_ids)

	# Retrieve documents by IDs from the collection
	retrieved_data = collection.get(ids=result_ids, include=['documents', 'embeddings'])

	# Extract the embedded texts and embeddings
	embedded_texts = retrieved_data['documents']
	embeddings = retrieved_data['embeddings']
	# Convert embeddings from numpy arrays to lists if needed
	embeddings = [embedding.tolist() 
			   if isinstance(embedding, np.ndarray) 
			   else embedding for embedding in embeddings]

	combined_text_chunks = ' '.join(embedded_texts)

	output_data = {
		"prompt": query + combined_text_chunks
	}

	# Output the data to a JSON file
	json_filename = "json_outputs/retrieved_data.json"
	with open(json_filename, 'w') as json_file:
		json.dump(output_data, json_file, indent=4)

	print(f"Data saved to {json_filename}")


def re_rank_results(text_results, image_results, text_weight=0.6, image_weight=0.4):
	"""
	Re-rank results based on weighted scores from both text and image searches.

	Args:
	- text_results (dict): The results from the text query.
	- image_results (dict): The results from the image query.
	- text_weight (float): The weight to apply to text scores (default 0.6).
	- image_weight (float): The weight to apply to image scores (default 0.4).

	Returns:
	- ranked_results (list): List of documents sorted by the combined weighted score.
	"""
	result_scores = {}

	# Combine scores from text results
	for idx, doc_id in enumerate(text_results["ids"][0]):  # Access list inside "ids"
		score = text_results["distances"][0][idx] * text_weight  # Access corresponding score
		if doc_id not in result_scores:
			result_scores[doc_id] = score
		else:
			result_scores[doc_id] += score  # If doc already exists, sum up the scores

	# Combine scores from image results
	for idx, doc_id in enumerate(image_results["ids"][0]):  # Access list inside "ids"
		score = image_results["distances"][0][idx] * image_weight  # Access corresponding score
		if doc_id not in result_scores:
			result_scores[doc_id] = score
		else:
			result_scores[doc_id] += score  # If doc already exists, sum up the scores

	# Sort the documents based on the combined score (lower distances mean better matches)
	sorted_results = sorted(result_scores.items(), key=lambda item: item[1])

	# Convert the sorted results into a list of dictionaries for better readability
	ranked_results = [{"id": doc_id, "score": score} for doc_id, score in sorted_results]

	return ranked_results


def upload():
	print("upload") 

	# Initialize GCS client
	storage_client = storage.Client()
	bucket = storage_client.bucket(BUCKET_NAME)

	# Bucket fold that will store chunks retrieval based on user inputs
	bucket_folder = "rag/rag_json_outputs"  

	# Get the list of JSON files in the local folder
	json_files = glob.glob(os.path.join(JSON_OUTPUT, "retrieved_data.json")) # *.json

	# Check if there are any JSON files to upload
	if not json_files:
		print("No JSON files found to upload.")
		return

	# Iterate over each JSON file and upload it
	for json_file in json_files:
		filename = os.path.basename(json_file)  # Get the file name
		destination_blob_name = os.path.join(bucket_folder, filename)  
		blob = bucket.blob(destination_blob_name)

		print(f"Uploading: {json_file} to {destination_blob_name}")

		# Upload the JSON file to GCS
		blob.upload_from_filename(json_file)

	print("Upload completed.")


def main(args=None):
	print("RAG Arguments:", args)

	if args.chunk:
		chunk()

	if args.embed:
		embed()

	if args.load: # pip install --upgrade chromadb
		load()

	if args.query:
		query()
		
	if args.download:
		download()

	if args.upload:
		upload()


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="RAG")

	parser.add_argument("--download", action="store_true", help="Download text files and image vectors from GCS bucket")
	parser.add_argument("--chunk", action="store_true", help="Chunk text")
	parser.add_argument("--embed", action="store_true", help="Generate embeddings")
	parser.add_argument("--load", action="store_true", help="Load embeddings to vector db")
	parser.add_argument("--query", action="store_true", help="Query vector db")
	parser.add_argument("--upload", action="store_true", help="Upload chunked texts in JSON to GCS bucket")
	args = parser.parse_args()

	main(args)