import { BookOpen, FileCode, Play, Upload } from 'lucide-react';

/**
 * Documentation Page — Dark glassmorphic sections
 */
const DocsPage = () => {
  const sections = [
    {
      icon: Upload,
      title: 'How to Upload Swagger',
      content: 'Drag and drop your JSON or YAML Swagger file into the upload box on the Testing page. Our system supports OpenAPI 3.0 and Swagger 2.0 formats. You can also paste a Swagger URL directly.',
    },
    {
      icon: FileCode,
      title: 'How to Fetch Test Cases',
      content: 'Once your file is uploaded, enter your API\'s Base URI (e.g., https://api.example.com/v1) and click "Generate Test Cases". The AI will analyze your schema and generate comprehensive test cases including edge cases and security checks.',
    },
    {
      icon: Play,
      title: 'How to Run Tests',
      content: 'Select the tests you wish to perform using the checkboxes and hit the "Run Tests" button. Results will appear in real-time as execution completes. Tests run in batches for optimal performance.',
    },
  ];

  return (
    <div className="max-w-4xl mx-auto py-12 px-6">
      <div className="text-center mb-10 animate-fadeInUp">
        <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-indigo-500/20 bg-indigo-500/10 text-indigo-300 text-sm font-medium mb-4">
          <BookOpen size={14} />
          Getting Started
        </div>
        <h1 className="text-4xl font-black gradient-text mb-3">Documentation</h1>
        <p className="text-slate-400 max-w-lg mx-auto">Learn how to use the autonomous testing and healing platform.</p>
      </div>

      <div className="space-y-4">
        {sections.map((s, i) => {
          const Icon = s.icon;
          return (
            <div
              key={i}
              className="glass-card glass-card-hover p-6 animate-fadeInUp"
              style={{ opacity: 0, animationDelay: `${0.1 + i * 0.1}s` }}
            >
              <div className="flex items-start gap-4">
                <div className="w-10 h-10 rounded-lg bg-indigo-500/15 flex items-center justify-center shrink-0">
                  <Icon size={20} className="text-indigo-400" />
                </div>
                <div>
                  <h2 className="text-xl font-bold text-white mb-2">{s.title}</h2>
                  <p className="text-slate-400 leading-relaxed">{s.content}</p>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default DocsPage;
