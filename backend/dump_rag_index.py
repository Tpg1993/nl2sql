import os
import json
from sqlalchemy import create_engine
from backend.metadata_rag import MetadataRAG

def dump_index():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(BASE_DIR, "ehr_data.db")
    engine = create_engine(f"sqlite:///{db_path}")
    
    # Instantiate RAG to build the index
    rag = MetadataRAG(engine)
    
    print("=== METADATA RAG INDEX SUMMARY ===")
    print(f"Total Tables Indexed: {len(rag.index['docs'])}\n")
    
    # We will write the detailed documents to a readable text file
    output_path = os.path.join(BASE_DIR, "rag_index_dump.txt")
    with open(output_path, "w") as f:
        f.write("=== METADATA RAG INDEXED DOCUMENTS ===\n")
        f.write("This file shows the list of stemmed keywords indexed for each table.\n")
        f.write("When you ask a question, the search engine matches query words to these keywords.\n\n")
        
        for table, doc in rag.index["docs"].items():
            f.write(f"Table: {table}\n")
            f.write("-" * 40 + "\n")
            f.write(f"Unique Stemmed Keywords: {sorted(list(doc['tf'].keys()))}\n")
            f.write(f"Term Frequencies (TF): {json.dumps(doc['tf'], indent=2)}\n")
            f.write("\n" + "=" * 60 + "\n\n")
            
    print(f"Saved human-readable RAG index dump to: {output_path}")

if __name__ == "__main__":
    dump_index()
