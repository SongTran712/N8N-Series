from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
import timm
import torch
from pymilvus import connections, MilvusClient
from io import BytesIO
import uvicorn
from openai import OpenAI
from pydantic import BaseModel
from urllib.request import urlopen

# Initialize FastAPI app
app = FastAPI()

# Connect to Milvus
connections.connect(alias="default", host="standalone", port="19530")
client = MilvusClient(uri="http://standalone:19530")
tokenizer = OpenAI(api_key="")

# Load model once on startup
model = timm.create_model(
    'tiny_vit_21m_224.dist_in22k_ft_in1k',
    pretrained=True,
    num_classes=0,
)
model.eval()

# Get transforms
data_config = timm.data.resolve_model_data_config(model)
transforms = timm.data.create_transform(**data_config, is_training=False)

@app.post("/search-image")
async def search_image(file: UploadFile = File(...)):
    try:
        # Read and convert image
        image_bytes = await file.read()
        image = Image.open(BytesIO(image_bytes)).convert("RGB")

        # Transform and run through model
        input_tensor = transforms(image).unsqueeze(0)  # Add batch dimension
        with torch.no_grad():
            features = model.forward_features(input_tensor)
            vector = model.forward_head(features, pre_logits=True)[0].tolist()

        # Search in Milvus
        result = client.search(
            collection_name="product_image",
            anns_field="image_retrieve_vector",
            data=[vector],
            limit=1,
            output_fields=["name, category"],
            search_params={"params": {"radius": 0.6}},
        )

        # Extract name
        names = []
        for hits in result:
            for hit in hits:
                names.append(hit['entity']['name'])

        if not names:
            return JSONResponse(content={"message": "No match found"}, status_code=404)

        return {"name": names[0]}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing image: {str(e)}")

class TextRequest(BaseModel):
    text: str
    image_urls: str
    
def parse_image_urls(data: str):
    # Remove square brackets if they exist
    data = data.strip("[]")
    # Split by comma and strip whitespace
    urls = [url.strip() for url in data.split(",") if url.strip()]
    return urls

def clean_image_urls(data):
    # Make sure it's a list
    if isinstance(data, list):
        # Remove surrounding quotes from each item if present
        return [url.strip('"') for url in data if isinstance(url, str)]
    return []

@app.post("/semantic-retrieve")
async def semantic_retrieve(req: TextRequest):
    # Get the input text
    text = req.text
    raw_img_urls = req.image_urls
    if isinstance(raw_img_urls, str):
        img_urls = parse_image_urls(raw_img_urls)
    elif isinstance(raw_img_urls, list):
        img_urls = raw_img_urls
    else:
        img_urls = []
    img_urls = clean_image_urls(img_urls)
    print(img_urls)
    responses = []
    if len(img_urls) > 0:
        
        for url in img_urls:
            with urlopen(url) as response:
                image = Image.open(BytesIO(response.read())).convert("RGB")


            # 3. Extract feature vector
            with torch.no_grad():
                features = model.forward_features(transforms(image).unsqueeze(0))
                hinh = model.forward_head(features, pre_logits=True)[0]
                query_vector = hinh.tolist()
                result = client.search(
                    collection_name="product_image",
                    anns_field="image_retrieve_vector",
                    data=[query_vector],
                    limit=1,
                    output_fields=["name", "category"],
                    search_params={"params": {"radius": 0.6}},
                )
                for hits in result:
                    for hit in hits:
                        responses.append(
                            {
                                'name':hit['entity']['name'],
                                'category': hit['entity']['category']
                            }   
                            
                        )

    if len(responses) == 0:
    # Create embedding
        response = tokenizer.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        query_vector = response.data[0].embedding

        # Search in the vector DB
        results = client.search(
            collection_name="products",
            data=[query_vector],
            anns_field="embedding",
            limit=3,
            search_params={
                "params": {
                    "radius": 0.4
                }
            },
            output_fields=["name", "catalog"]
        )

  
        for hit in results[0]:
            entity = hit.get("entity", {})
            name = entity.get("name", "")
            catalog = entity.get("catalog", "")
            responses.append({
                "name": name,
                "category   ": catalog
            })

    return responses
# Run with: uvicorn service:app --host 0.0.0.0 --port 7123
