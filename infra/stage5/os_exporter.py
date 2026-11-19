"""Bounded read-only OpenSearch REST metrics for the local single-node stack."""
import json
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        metrics={"opensearch_up":0}
        try:
            def get(path):
                with urllib.request.urlopen("http://opensearch:9200"+path,timeout=2) as r:return json.load(r)
            health=get("/_cluster/health")
            stats=get("/_nodes/stats/jvm,process,fs,indices")
            nodes=list(stats["nodes"].values())
            metrics.update({"opensearch_up":1,"opensearch_cluster_health":{ "red":0,"yellow":1,"green":2}[health["status"]],"opensearch_jvm_heap_used_bytes":sum(n["jvm"]["mem"]["heap_used_in_bytes"] for n in nodes),"opensearch_process_cpu_percent":sum(n["process"]["cpu"]["percent"] for n in nodes),"opensearch_fs_available_bytes":min(n["fs"]["total"]["available_in_bytes"] for n in nodes),"opensearch_index_documents":sum(n["indices"]["docs"]["count"] for n in nodes)})
        except Exception:pass
        body="".join(f"# TYPE {k} gauge\n{k} {v}\n" for k,v in metrics.items()).encode()
        self.send_response(200);self.send_header("Content-Type","text/plain; version=0.0.4");self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass


HTTPServer(("0.0.0.0",9116),Handler).serve_forever()
