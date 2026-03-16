import { useMemo, useState } from "react";
import axios from "axios";
import { Download, FileSpreadsheet, ImagePlus, Moon, Sparkles, Sun } from "lucide-react";
import { Toaster, toast } from "sonner";
import "@/App.css";
import PinCard from "@/components/PinCard";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

const textPositionOptions = ["top", "center", "bottom"];

function App() {
  const [excelFile, setExcelFile] = useState(null);
  const [templateFile, setTemplateFile] = useState(null);
  const [textPosition, setTextPosition] = useState("center");
  const [progressValue, setProgressValue] = useState(0);
  const [isGenerating, setIsGenerating] = useState(false);
  const [pins, setPins] = useState([]);
  const [sessionId, setSessionId] = useState("");
  const [isDarkMode, setIsDarkMode] = useState(false);

  const generatedCount = useMemo(() => pins.length, [pins]);

  const toggleTheme = () => {
    const updated = !isDarkMode;
    setIsDarkMode(updated);
    document.documentElement.classList.toggle("dark", updated);
  };

  const handleGenerate = async () => {
    if (!excelFile) {
      toast.error("Please upload an Excel or CSV file first.");
      return;
    }

    let generationSucceeded = false;
    try {
      setIsGenerating(true);
      setProgressValue(5);
      const progressTimer = setInterval(() => {
        setProgressValue((current) => (current < 92 ? current + 6 : current));
      }, 350);

      const formData = new FormData();
      formData.append("data_file", excelFile);
      if (templateFile) {
        formData.append("template_image", templateFile);
      }
      formData.append("template_text_position", textPosition);
      formData.append("max_pins", "500");

      const response = await axios.post(`${API}/pins/generate`, formData, {
        headers: {
          "Content-Type": "multipart/form-data",
        },
      });

      clearInterval(progressTimer);
      setProgressValue(100);
      setPins(response.data.pins || []);
      setSessionId(response.data.session_id || "");
      generationSucceeded = true;
      toast.success(`Generated ${response.data.total_generated} Pinterest pins successfully.`);
    } catch (error) {
      const message = error?.response?.data?.detail || "Pin generation failed. Please check your file format.";
      setProgressValue(0);
      toast.error(message);
    } finally {
      setIsGenerating(false);
      if (!generationSucceeded) {
        setPins([]);
        setSessionId("");
      }
    }
  };

  const handleDownloadAll = () => {
    if (!sessionId) {
      toast.error("Generate pins first to enable ZIP download.");
      return;
    }
    window.open(`${API}/pins/download-all/${sessionId}`, "_blank", "noopener,noreferrer");
  };

  const handleExportMetadata = (exportFormat) => {
    if (!sessionId) {
      toast.error("Generate pins first to export metadata.");
      return;
    }
    window.open(`${API}/pins/export/${sessionId}?export_format=${exportFormat}`, "_blank", "noopener,noreferrer");
  };

  return (
    <div
      className="min-h-screen bg-gradient-to-br from-red-50 via-white to-slate-50 dark:from-slate-950 dark:via-slate-900 dark:to-slate-950"
      data-testid="pin-generator-app"
    >
      <Toaster richColors position="top-right" />
      <main className="mx-auto flex w-full max-w-7xl flex-col gap-8 px-4 py-10 lg:px-8">
        <header className="rounded-3xl border border-red-100 bg-white/80 p-6 shadow-[0_15px_40px_rgba(239,68,68,0.08)] backdrop-blur dark:border-slate-700 dark:bg-slate-900/80 dark:shadow-[0_15px_40px_rgba(2,6,23,0.45)]">
          <div className="flex flex-wrap items-start justify-between gap-5">
            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-red-600 dark:text-red-400" data-testid="header-kicker-text">
                Pinterest Affiliate Workflow
              </p>
              <h1 className="heading-font text-4xl font-bold leading-tight text-slate-900 sm:text-5xl lg:text-6xl dark:text-slate-100" data-testid="app-main-heading">
                Bulk Pin Creator Studio
              </h1>
              <p className="max-w-2xl text-sm text-slate-600 sm:text-base dark:text-slate-300" data-testid="app-subheading-text">
                Upload Excel, auto-build prompts, generate up to 500 pin creatives, and export metadata for future Pinterest automation.
              </p>
            </div>
            <Button
              onClick={toggleTheme}
              variant="outline"
              className="rounded-full border-slate-300 bg-white dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
              data-testid="theme-toggle-button"
            >
              {isDarkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              {isDarkMode ? "Light" : "Dark"} Mode
            </Button>
          </div>
        </header>

        <section className="grid gap-8 lg:grid-cols-[320px_1fr]" data-testid="dashboard-main-grid">
          <Card className="h-fit rounded-3xl border border-slate-200 bg-white/90 shadow-[0_8px_32px_rgba(15,23,42,0.1)] dark:border-slate-700 dark:bg-slate-900/90">
            <CardHeader>
              <CardTitle className="heading-font text-2xl text-slate-900 dark:text-slate-100" data-testid="upload-panel-title">
                Upload & Generate
              </CardTitle>
              <CardDescription data-testid="upload-panel-description">
                Supports .xlsx and .csv with required columns.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="space-y-2">
                <Label htmlFor="excel-file" data-testid="excel-upload-label">Excel / CSV file</Label>
                <Input
                  id="excel-file"
                  type="file"
                  accept=".xlsx,.csv"
                  onChange={(event) => setExcelFile(event.target.files?.[0] || null)}
                  data-testid="excel-upload-input"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="template-file" data-testid="template-upload-label">Template image (optional)</Label>
                <Input
                  id="template-file"
                  type="file"
                  accept="image/*"
                  onChange={(event) => setTemplateFile(event.target.files?.[0] || null)}
                  data-testid="template-upload-input"
                />
              </div>

              <div className="space-y-2">
                <Label data-testid="text-position-label">Template quote position</Label>
                <Select value={textPosition} onValueChange={setTextPosition}>
                  <SelectTrigger className="bg-white dark:bg-slate-900" data-testid="text-position-select-trigger">
                    <SelectValue placeholder="Select text position" />
                  </SelectTrigger>
                  <SelectContent>
                    {textPositionOptions.map((option) => (
                      <SelectItem
                        key={option}
                        value={option}
                        data-testid={`text-position-select-option-${option}`}
                      >
                        {option.charAt(0).toUpperCase() + option.slice(1)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <Button
                onClick={handleGenerate}
                disabled={isGenerating}
                className="h-11 w-full rounded-full bg-[#E60023] text-base font-semibold text-white hover:bg-[#AD081B]"
                data-testid="generate-pins-button"
              >
                {isGenerating ? (
                  <>
                    <Sparkles className="h-4 w-4 animate-pulse" />
                    Generating...
                  </>
                ) : (
                  <>
                    <ImagePlus className="h-4 w-4" />
                    Generate Pins
                  </>
                )}
              </Button>
            </CardContent>
          </Card>

          <div className="space-y-8">
            <Card className="rounded-3xl border border-slate-200 bg-white/90 shadow-[0_8px_32px_rgba(15,23,42,0.08)] dark:border-slate-700 dark:bg-slate-900/90">
              <CardHeader>
                <CardTitle className="heading-font text-2xl text-slate-900 dark:text-slate-100" data-testid="generation-status-title">
                  Generation Status
                </CardTitle>
                <CardDescription data-testid="generation-status-subtitle">
                  Real-time progress for bulk rendering up to 500 Pinterest pins.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <Progress
                  value={progressValue}
                  className="h-3 bg-slate-100 dark:bg-slate-800 [&>div]:bg-[#E60023]"
                  data-testid="pin-generation-progress-bar"
                />
                <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-700 dark:text-slate-200">
                  <span data-testid="progress-value-text">Progress: {Math.round(progressValue)}%</span>
                  <span data-testid="generated-count-text">Generated Pins: {generatedCount}</span>
                  <span className="truncate" data-testid="generation-session-id-text">
                    Session: {sessionId || "Not started"}
                  </span>
                </div>
                <div className="flex flex-wrap gap-3 pt-2">
                  <Button
                    onClick={handleDownloadAll}
                    variant="outline"
                    className="rounded-full border-slate-300 bg-white dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                    data-testid="download-all-zip-button"
                  >
                    <Download className="h-4 w-4" />
                    Download ZIP
                  </Button>
                  <Button
                    onClick={() => handleExportMetadata("csv")}
                    variant="outline"
                    className="rounded-full border-slate-300 bg-white dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                    data-testid="export-metadata-csv-button"
                  >
                    <FileSpreadsheet className="h-4 w-4" />
                    Export CSV
                  </Button>
                  <Button
                    onClick={() => handleExportMetadata("json")}
                    variant="outline"
                    className="rounded-full border-slate-300 bg-white dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                    data-testid="export-metadata-json-button"
                  >
                    <FileSpreadsheet className="h-4 w-4" />
                    Export JSON
                  </Button>
                </div>
              </CardContent>
            </Card>

            <section className="space-y-5" data-testid="preview-grid-section">
              <div className="flex items-center justify-between gap-4">
                <h2 className="heading-font text-base font-semibold text-slate-900 md:text-lg dark:text-slate-100" data-testid="preview-grid-title">
                  Pin Preview Dashboard
                </h2>
                <p className="text-sm text-slate-600 dark:text-slate-300" data-testid="preview-grid-count-text">
                  {generatedCount} pin(s)
                </p>
              </div>

              {pins.length === 0 ? (
                <div
                  className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center text-sm text-slate-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-300"
                  data-testid="preview-empty-state"
                >
                  Generate pins to see previews, download images, and export metadata.
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-3" data-testid="preview-grid-list">
                  {pins.map((pin) => (
                    <PinCard key={pin.pin_id} pin={pin} backendUrl={BACKEND_URL} />
                  ))}
                </div>
              )}
            </section>
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;
