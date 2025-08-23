-- Database Schema for Vietnam Hearts Knowledge Base
-- Run this in your Supabase SQL Editor

-- 1. Create the document_chunks table
CREATE TABLE IF NOT EXISTS public.document_chunks (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    content TEXT NOT NULL,
    embedding vector(384), -- all-MiniLM-L6-v2 dimension (corrected from 768)
    source_document_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 2. Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_document_chunks_source_doc ON public.document_chunks(source_document_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_created_at ON public.document_chunks(created_at);
CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding ON public.document_chunks USING ivfflat (embedding vector_cosine_ops);

-- 3. Create the match_documents function for vector similarity search
CREATE OR REPLACE FUNCTION public.match_documents(
    query_embedding vector(384), -- all-MiniLM-L6-v2 dimension (corrected from 768)
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 3
)
RETURNS TABLE (
    id UUID,
    content TEXT,
    source_document_id TEXT,
    chunk_index INTEGER,
    metadata JSONB,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        dc.id,
        dc.content,
        dc.source_document_id,
        dc.chunk_index,
        dc.metadata,
        1 - (dc.embedding <=> query_embedding) AS similarity
    FROM public.document_chunks dc
    WHERE 1 - (dc.embedding <=> query_embedding) > match_threshold
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- 4. Enable vector extension if not already enabled
CREATE EXTENSION IF NOT EXISTS vector;

-- 5. Grant permissions to authenticated users
GRANT ALL ON public.document_chunks TO authenticated;
GRANT EXECUTE ON FUNCTION public.match_documents TO authenticated;

-- 6. Create a function to update the updated_at timestamp
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- 7. Create trigger to automatically update updated_at
CREATE TRIGGER update_document_chunks_updated_at 
    BEFORE UPDATE ON public.document_chunks 
    FOR EACH ROW 
    EXECUTE FUNCTION public.update_updated_at_column();

-- 8. Insert a sample document chunk for testing (optional)
-- INSERT INTO public.document_chunks (content, source_document_id, chunk_index, metadata) 
-- VALUES ('This is a test chunk for Vietnam Hearts knowledge base.', 'test_doc', 0, '{"title": "Test Document", "type": "test"}');

-- 9. Verify the setup
SELECT 'Schema setup complete!' as status;
SELECT COUNT(*) as chunks_count FROM public.document_chunks;
SELECT routine_name FROM information_schema.routines WHERE routine_schema = 'public' AND routine_name = 'match_documents';

-- 10. Important Note
-- The all-MiniLM-L6-v2 model produces 384-dimensional embeddings, not 768
-- This schema is correctly configured for that model
