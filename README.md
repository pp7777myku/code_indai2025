Python 3.10+

docker build -t my-gemini-app .

docker run -d -p 8000:8000 -e GEMINI_API_KEY="***" -e HTTP_PROXY="http://172.17.0.1:20171" -e HTTPS_PROXY="http://172.17.0.1:20171" --name gemini-service my-gemini-app
