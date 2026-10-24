"""OpenTelemetry local evidence, with opt-in OTLP export for W9."""
import json,threading
from pathlib import Path
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor,SpanExporter,SpanExportResult
from opentelemetry.sdk.resources import Resource

class FileExporter(SpanExporter):
    def __init__(self,path):self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.lock=threading.Lock()
    def export(self,spans):
        with self.lock,self.path.open('a') as f:
            for s in spans:
                value={'trace_id':format(s.context.trace_id,'032x'),'span_id':format(s.context.span_id,'016x'),'parent_span_id':format(s.parent.span_id,'016x') if s.parent else None,'name':s.name,'start_time':s.start_time,'end_time':s.end_time,'attributes':dict(s.attributes),'status':s.status.status_code.name}
                f.write(json.dumps(value,ensure_ascii=False)+'\n')
        return SpanExportResult.SUCCESS
    def shutdown(self):pass

def setup(path,endpoint=None,headers=None):
    provider=TracerProvider(resource=Resource.create({'service.name':'fde-member-agent','service.version':'0.2.0-w1'}))
    provider.add_span_processor(SimpleSpanProcessor(FileExporter(path)))
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint,headers=headers or {},timeout=5)))
    return provider,provider.get_tracer('fde-product')
