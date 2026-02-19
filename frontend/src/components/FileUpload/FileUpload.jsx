import { CloudUpload, FileCode } from 'lucide-react';
import { useRef } from 'react';

/**
 * File Upload Component — Dark glassmorphic upload zone
 */
const FileUpload = ({ uploadedFile, onFileChange }) => {
  const fileInputRef = useRef(null);
  const triggerUpload = () => fileInputRef.current.click();

  return (
    <div
      onClick={triggerUpload}
      className="glass-card glass-card-hover border-dashed border-2 border-white/10 p-10 text-center group cursor-pointer transition-all duration-300 hover:border-indigo-500/30"
    >
      <input
        type="file"
        className="hidden"
        ref={fileInputRef}
        onChange={onFileChange}
        accept=".json,.yaml,.yml"
      />
      <div className="flex flex-col items-center">
        {uploadedFile ? (
          <>
            <div className="w-14 h-14 rounded-xl bg-emerald-500/15 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
              <FileCode className="w-7 h-7 text-emerald-400" />
            </div>
            <h2 className="text-lg font-bold text-white mb-1">{uploadedFile.name}</h2>
            <p className="text-slate-500 text-sm">File ready. Click to change.</p>
          </>
        ) : (
          <>
            <div className="w-14 h-14 rounded-xl bg-indigo-500/15 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
              <CloudUpload className="w-7 h-7 text-indigo-400" />
            </div>
            <h2 className="text-lg font-bold text-white mb-1">Upload File</h2>
            <p className="text-slate-500 text-sm">Click to upload Swagger/OpenAPI JSON or YAML</p>
          </>
        )}
      </div>
    </div>
  );
};

export default FileUpload;
