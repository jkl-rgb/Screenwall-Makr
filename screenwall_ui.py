"""
screenwall_ui.py
-----------------

This module provides a simple graphical user interface (GUI) for the
screenwall generator using Python's built‑in ``tkinter`` library.  The
GUI allows users to select an input CSV file describing a batch of
perforated panels, choose an output directory for the generated DXF
files and optionally specify stock sheet dimensions for nesting.  It
wraps the core functions from ``screenwall_generator.py`` so that
non‑programmers can run the generator without using the command line.

Key features:

* CSV file selection:  Users can browse their filesystem to choose
  the CSV file containing panel definitions.  The expected columns
  match those documented in ``screenwall_generator.parse_csv``.

* Output directory selection:  Users can choose where the resulting
  DXF files will be written.  The directory will be created if it
  does not already exist.

* Nesting options:  A checkbox enables or disables nesting.  When
  nesting is enabled, users may specify the stock sheet width and
  height (in inches).  A combined nesting DXF will be created in
  addition to the individual panel files.

* Progress feedback:  The application displays a message upon
  successful generation of all files or shows an error dialog if
  something goes wrong.

Usage:

Running this script launches a window with controls for selecting
inputs and executing the generator.  The tool assumes that the
``screenwall_generator.py`` module is in the same directory or in
the Python path.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    # Import generator functions from the sibling module
    from screenwall_generator import parse_csv, generate_panel_dxf, nest_panels, write_nesting_dxf
except ImportError:
    # If import fails, tell the user
    raise ImportError(
        "screenwall_generator module not found. Please ensure it is "
        "available in the same directory as this UI script."
    )


class ScreenwallUI(tk.Tk):
    """Main application window for the screenwall generator GUI."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Screenwall Generator UI")
        self.geometry("520x260")
        self.resizable(False, False)

        # Input file selection
        tk.Label(self, text="Input CSV file:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.csv_path_var = tk.StringVar()
        tk.Entry(self, textvariable=self.csv_path_var, width=40).grid(row=0, column=1, padx=5, pady=10, sticky="w")
        tk.Button(self, text="Browse…", command=self.browse_csv).grid(row=0, column=2, padx=5, pady=10)

        # Output directory selection
        tk.Label(self, text="Output directory:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        self.output_dir_var = tk.StringVar()
        tk.Entry(self, textvariable=self.output_dir_var, width=40).grid(row=1, column=1, padx=5, pady=10, sticky="w")
        tk.Button(self, text="Browse…", command=self.browse_output_dir).grid(row=1, column=2, padx=5, pady=10)

        # Nesting checkbox and stock dimensions
        self.nest_var = tk.BooleanVar(value=False)
        nest_check = tk.Checkbutton(self, text="Generate nesting layout", variable=self.nest_var, command=self.toggle_nesting_fields)
        nest_check.grid(row=2, column=0, padx=10, pady=10, sticky="w")

        tk.Label(self, text="Stock width (in):").grid(row=3, column=0, padx=10, pady=5, sticky="w")
        self.stock_width_var = tk.StringVar(value="48.0")
        self.stock_width_entry = tk.Entry(self, textvariable=self.stock_width_var, width=10, state="disabled")
        self.stock_width_entry.grid(row=3, column=1, padx=5, pady=5, sticky="w")

        tk.Label(self, text="Stock height (in):").grid(row=4, column=0, padx=10, pady=5, sticky="w")
        self.stock_height_var = tk.StringVar(value="96.0")
        self.stock_height_entry = tk.Entry(self, textvariable=self.stock_height_var, width=10, state="disabled")
        self.stock_height_entry.grid(row=4, column=1, padx=5, pady=5, sticky="w")

        # Run button
        tk.Button(self, text="Generate DXF", command=self.run_generator, width=20).grid(row=5, column=0, columnspan=3, pady=20)

    def browse_csv(self) -> None:
        """Open a file dialog to select the input CSV file."""
        filename = filedialog.askopenfilename(
            title="Select CSV file",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if filename:
            self.csv_path_var.set(filename)

    def browse_output_dir(self) -> None:
        """Open a directory dialog to select the output location."""
        directory = filedialog.askdirectory(title="Select output directory")
        if directory:
            self.output_dir_var.set(directory)

    def toggle_nesting_fields(self) -> None:
        """Enable or disable stock dimension entries based on nesting checkbox."""
        if self.nest_var.get():
            self.stock_width_entry.configure(state="normal")
            self.stock_height_entry.configure(state="normal")
        else:
            self.stock_width_entry.configure(state="disabled")
            self.stock_height_entry.configure(state="disabled")

    def run_generator(self) -> None:
        """Parse inputs and generate DXF files using the generator functions."""
        csv_path = self.csv_path_var.get().strip()
        output_dir = self.output_dir_var.get().strip()
        if not csv_path:
            messagebox.showerror("Error", "Please select an input CSV file.")
            return
        if not output_dir:
            messagebox.showerror("Error", "Please select an output directory.")
            return
        try:
            panels = parse_csv(csv_path)
            if not panels:
                messagebox.showinfo("Info", "No valid panels found in the CSV (holes < 0.125\" may be skipped).")
                return
            # Ensure output directory exists
            os.makedirs(output_dir, exist_ok=True)
            # Generate individual panels
            for spec in panels:
                generate_panel_dxf(spec, output_dir)
            # Optional nesting
            if self.nest_var.get():
                try:
                    stock_w = float(self.stock_width_var.get())
                    stock_h = float(self.stock_height_var.get())
                except ValueError:
                    messagebox.showerror("Error", "Stock dimensions must be numeric.")
                    return
                try:
                    sheets = nest_panels(panels, stock_w, stock_h)
                    panel_dict = {spec.panel_id: spec for spec in panels}
                    write_nesting_dxf(sheets, panel_dict, output_dir)
                except ValueError as e:
                    messagebox.showerror("Nesting Error", str(e))
                    return
            messagebox.showinfo("Success", "DXF files generated successfully.")
        except Exception as exc:
            messagebox.showerror("Error", str(exc))


def main() -> None:
    """Entry point for running the GUI."""
    app = ScreenwallUI()
    app.mainloop()


if __name__ == "__main__":
    main()