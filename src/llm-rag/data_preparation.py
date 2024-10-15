import os
import json
import random
import argparse


DATA_FOLDER = "data_prep/data_llm"  # Folder where 'train.jsonl', 'validation.jsonl', 'test.jsonl' will be created
OUTPUT_FOLDER = "data_prep"  # Folder where all JSON files are located
INPUT_FOLDER = "input_datasets"

def split_json_to_jsonl(input_folder, output_folder, train_ratio=0.8, val_ratio=0.1, test_ratio=0.1):
    # Get a list of all JSON files in the input folder
    json_files = [f for f in os.listdir(input_folder) if f.endswith('.json')]

    # Shuffle the files to ensure randomness
    random.shuffle(json_files)

    # Calculate the number of files for each split
    total_files = len(json_files)
    train_count = int(total_files * train_ratio)
    val_count = int(total_files * val_ratio)
    test_count = total_files - train_count - val_count  # Remaining files go to test

    # Split the files into train, validation, and test sets
    train_files = json_files[:train_count]
    val_files = json_files[train_count:train_count + val_count]
    test_files = json_files[train_count + val_count:]

    # Create output folder if it doesn't exist
    os.makedirs(output_folder, exist_ok=True)

    # Define output JSONL files
    train_jsonl = os.path.join(output_folder, "train.jsonl")
    val_jsonl = os.path.join(output_folder, "validation.jsonl")
    test_jsonl = os.path.join(output_folder, "test.jsonl")

    # Function to write JSON files to a JSONL file
    def write_to_jsonl(files, jsonl_filename):
        with open(jsonl_filename, 'w') as jsonl_file:
            for json_file in files:
                with open(os.path.join(input_folder, json_file), 'r') as f:
                    data = json.load(f)  # Load the JSON content
                    jsonl_file.write(json.dumps(data) + '\n')  # Write each JSON object as a new line
        print(f"{len(files)} files written to {jsonl_filename}")

    # Write to train, validation, and test JSONL files
    write_to_jsonl(train_files, train_jsonl)
    write_to_jsonl(val_files, val_jsonl)
    write_to_jsonl(test_files, test_jsonl)



# split_json_to_jsonl(input_folder, output_folder, train_ratio=0.8, val_ratio=0.1, test_ratio=0.1)


def data_prep_query():
    client = chromadb.HttpClient(host=CHROMADB_HOST, port=CHROMADB_PORT)
    collection_name = "semantic-text-image-collection"
    collection = client.get_collection(name=collection_name)

    # Perform text query
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

    # Load the user input image query embedding (which is 1024-dimensional)
    image_folder = os.path.join(INPUT_FOLDER, "image_vectors")  # Folder containing image embeddings
    text_folder = os.path.join(INPUT_FOLDER, "text_instructions/txt_outputs")   # Folder containing the text instructions
    image_files = sorted(os.listdir(image_folder))  # Ensure files are sorted for matching with text

    for image_file in image_files:
        # Load the image embedding for the current image file
        image_embedding_path = os.path.join(image_folder, image_file)
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

        # Re-rank the results based on both text and image queries
        ranked_results = re_rank_results(text_results, image_results, text_weight=0.6, image_weight=0.4)

        # Extract document IDs from ranked results
        result_ids = [result['id'] for result in ranked_results]

        # Retrieve documents by IDs from the collection
        retrieved_data = collection.get(ids=result_ids, include=['documents', 'embeddings'])

        # Extract the embedded texts and embeddings
        embedded_texts = retrieved_data['documents']
        embeddings = retrieved_data['embeddings']

        # Convert embeddings from numpy arrays to lists if needed
        embeddings = [embedding.tolist() if isinstance(embedding, np.ndarray) else embedding for embedding in embeddings]

        # Load the corresponding text file for the current image file
        text_file = os.path.splitext(image_file)[0] + ".txt"
        text_file_path = os.path.join(text_folder, text_file)

        if os.path.exists(text_file_path):
            with open(text_file_path, "r") as f:
                text_instruction = f.read()
        else:
            text_instruction = "Text instruction not found."

        # Prepare the data for the JSON format
        output_data = {
            "input": {
                "image_embeddings": [image_query_embedding.tolist()],  # Image vectors from .npy
                "text_chunk_embeddings": embeddings  # Text embeddings (chunks) from ChromaDB
            },
            "output": text_instruction  # Corresponding text instruction
        }

        # Save the data to an individual JSON file for each image
        json_filename = f"data_prep/{os.path.splitext(image_file)[0]}_output.json"
        with open(json_filename, 'w') as json_file:
            json.dump(output_data, json_file, indent=4)

        print(f"Data for {image_file} saved to {json_filename}")\


def main(args=None):
	print("CLI Arguments:", args)

	if args.split:
		split_json_to_jsonl(OUTPUT_FOLDER, DATA_FOLDER)

	if args.prep:
		data_prep_query()()



if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="CLI")

	parser.add_argument("--split", action="store_true", help="Split the data into train, test, and validation")
	parser.add_argument("--prep", action="store_true", help="Prepared the dataset")
	args = parser.parse_args()

	main(args)