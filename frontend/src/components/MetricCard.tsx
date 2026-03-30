type MetricCardProps = {
  label: string;
  value: string;
  detail?: string;
  tone?: "calm" | "accent" | "warn";
};

export function MetricCard({ label, value, detail, tone = "calm" }: MetricCardProps) {
  return (
    <section className={`metric-card ${tone}`}>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      {detail ? <div className="metric-detail">{detail}</div> : null}
    </section>
  );
}
